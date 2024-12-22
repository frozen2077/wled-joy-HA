"""Support for LED lights."""

from __future__ import annotations

from functools import partial
from typing import Any, cast
import math
import logging


from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,    
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.color as color_util
from homeassistant.util.color import (
    color_temperature_kelvin_to_mired as kelvin_to_mired,
    color_temperature_mired_to_kelvin as mired_to_kelvin,
)

_LOGGER = logging.getLogger(__name__)

from . import WLEDConfigEntry
from .const import (
    ATTR_CCT,
    ATTR_COLOR_PRIMARY,
    ATTR_ON,
    ATTR_SEGMENT_ID,
    COLOR_TEMP_K_MAX,
    COLOR_TEMP_K_MIN,
    LIGHT_CAPABILITIES_COLOR_MODE_MAPPING,
)
from .coordinator import WLEDDataUpdateCoordinator
from .entity import WLEDEntity
from .helpers import kelvin_to_255, kelvin_to_255_reverse, wled_exception_handler

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WLEDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WLED light based on a config entry."""
    coordinator = entry.runtime_data
    if coordinator.keep_main_light:
        async_add_entities([WLEDMainLight(coordinator=coordinator)])

    update_segments = partial(
        async_update_segments,
        coordinator,
        set(),
        async_add_entities,
    )

    coordinator.async_add_listener(update_segments)
    update_segments()


class WLEDMainLight(WLEDEntity, LightEntity):
    """Defines a WLED main light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_translation_key = "main"
    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: WLEDDataUpdateCoordinator) -> None:
        """Initialize WLED main light."""
        super().__init__(coordinator=coordinator)
        self._attr_unique_id = coordinator.data.info.mac_address

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 1..255."""
        return self.coordinator.data.state.brightness

    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        return bool(self.coordinator.data.state.on)

    @property
    def available(self) -> bool:
        """Return if this main light is available or not."""
        return self.coordinator.has_main_light and super().available

    @wled_exception_handler
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        transition = None
        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            transition = round(kwargs[ATTR_TRANSITION] * 10)

        await self.coordinator.wled.master(on=False, transition=transition)

    @wled_exception_handler
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        transition = None
        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            transition = round(kwargs[ATTR_TRANSITION] * 10)

        await self.coordinator.wled.master(
            on=True, brightness=kwargs.get(ATTR_BRIGHTNESS), transition=transition
        )


class WLEDSegmentLight(WLEDEntity, LightEntity):
    """Defines a WLED light based on a segment."""

    _attr_supported_features = LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
    _attr_translation_key = "segment"
    _attr_min_color_temp_kelvin = COLOR_TEMP_K_MIN
    _attr_max_color_temp_kelvin = COLOR_TEMP_K_MAX
    _attr_min_mireds = kelvin_to_mired(COLOR_TEMP_K_MIN)
    _attr_max_mireds = kelvin_to_mired(COLOR_TEMP_K_MAX)

    def __init__(
        self,
        coordinator: WLEDDataUpdateCoordinator,
        segment: int,
    ) -> None:
        """Initialize WLED segment light."""
        super().__init__(coordinator=coordinator)
        self._segment = segment
        self._ct = 300

        # Segment 0 uses a simpler name, which is more natural for when using
        # a single segment / using WLED with one big LED strip.
        if segment == 0:
            self._attr_name = None
        else:
            self._attr_translation_placeholders = {"segment": str(segment)}

        self._attr_unique_id = (
            f"{self.coordinator.data.info.mac_address}_{self._segment}"
        )

        if (
            coordinator.data.info.leds.segment_light_capabilities is not None
            and (
                color_modes := LIGHT_CAPABILITIES_COLOR_MODE_MAPPING.get(
                    coordinator.data.info.leds.segment_light_capabilities[segment]
                )
            )
            is not None
        ):
            self._attr_color_mode = color_modes[0]
            self._attr_supported_color_modes = set(color_modes)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        try:
            self.coordinator.data.state.segments[self._segment]
        except KeyError:
            return False

        return super().available

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the color value."""
        if not (color := self.coordinator.data.state.segments[self._segment].color):
            return None
        return color.primary[:3]

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the color value."""
        if not (color := self.coordinator.data.state.segments[self._segment].color):
            return None
        return cast(tuple[int, int, int, int], color.primary)

    @property
    def color_temp(self) -> int | None:
        """Return the color temperature."""
        if self._attr_color_mode == ColorMode.COLOR_TEMP :
            return self._ct
        else :
            return 300

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the CT color value in K."""
        cct = self.coordinator.data.state.segments[self._segment].cct
        return kelvin_to_255_reverse(cct, COLOR_TEMP_K_MIN, COLOR_TEMP_K_MAX)

    @property
    def effect(self) -> str | None:
        """Return the current effect of the light."""
        return self.coordinator.data.effects[
            int(self.coordinator.data.state.segments[self._segment].effect_id)
        ].name

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 1..255."""
        state = self.coordinator.data.state

        # If this is the one and only segment, calculate brightness based
        # on the main and segment brightness
        if not self.coordinator.has_main_light:
            return int(
                (state.segments[self._segment].brightness * state.brightness) / 255
            )

        return state.segments[self._segment].brightness

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects."""
        # return [effect.name for effect in self.coordinator.data.effects.values()]
        return [preset.name for preset in self.coordinator.data.presets.values()]
    
    @property
    def is_on(self) -> bool:
        """Return the state of the light."""
        state = self.coordinator.data.state

        # If there is no main, we take the main state into account
        # on the segment level.
        if not self.coordinator.has_main_light and not state.on:
            return False

        return bool(state.segments[self._segment].on)

    @wled_exception_handler
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        transition = None
        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            transition = round(kwargs[ATTR_TRANSITION] * 10)

        # If there is no main control, and only 1 segment, handle the main
        if not self.coordinator.has_main_light:
            await self.coordinator.wled.master(on=False, transition=transition)
            return

        await self.coordinator.wled.segment(
            segment_id=self._segment, on=False, transition=transition
        )

    @wled_exception_handler
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        data: dict[str, Any] = {
            ATTR_ON: True,
            ATTR_SEGMENT_ID: self._segment,
        }

        if ATTR_RGB_COLOR in kwargs:
            data[ATTR_COLOR_PRIMARY] = kwargs[ATTR_RGB_COLOR]
            self._attr_color_mode = ColorMode.RGB

        if ATTR_RGBW_COLOR in kwargs:
            data[ATTR_COLOR_PRIMARY] = kwargs[ATTR_RGBW_COLOR]
            self._attr_color_mode = ColorMode.RGBW

        if ATTR_COLOR_TEMP in kwargs:       
            data[ATTR_COLOR_PRIMARY] = convert_K_to_RGB(mired_to_kelvin((kwargs[ATTR_COLOR_TEMP])))
            self._ct = kwargs[ATTR_COLOR_TEMP]
            self._attr_color_mode = ColorMode.COLOR_TEMP

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            data[ATTR_CCT] = kelvin_to_255(
                kwargs[ATTR_COLOR_TEMP_KELVIN], COLOR_TEMP_K_MIN, COLOR_TEMP_K_MAX
            )

        if ATTR_TRANSITION in kwargs:
            # WLED uses 100ms per unit, so 10 = 1 second.
            data[ATTR_TRANSITION] = round(kwargs[ATTR_TRANSITION] * 10)

        if ATTR_BRIGHTNESS in kwargs:
            data[ATTR_BRIGHTNESS] = kwargs[ATTR_BRIGHTNESS]

        if ATTR_EFFECT in kwargs:
            data[ATTR_EFFECT] = kwargs[ATTR_EFFECT]
            if kwargs[ATTR_EFFECT] != 'Solid' :
                await self.coordinator.wled.preset(preset=kwargs[ATTR_EFFECT])
                return    
            
        # If there is no main control, and only 1 segment, handle the main
        if not self.coordinator.has_main_light:
            main_data = {ATTR_ON: True}
            if ATTR_BRIGHTNESS in data:
                main_data[ATTR_BRIGHTNESS] = data[ATTR_BRIGHTNESS]
                data[ATTR_BRIGHTNESS] = 255

            if ATTR_TRANSITION in data:
                main_data[ATTR_TRANSITION] = data[ATTR_TRANSITION]
                del data[ATTR_TRANSITION]

            await self.coordinator.wled.segment(**data)
            await self.coordinator.wled.master(**main_data)
            return

        await self.coordinator.wled.segment(**data)


@callback
def async_update_segments(
    coordinator: WLEDDataUpdateCoordinator,
    current_ids: set[int],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Update segments."""
    segment_ids = {
        light.segment_id
        for light in coordinator.data.state.segments.values()
        if light.segment_id is not None
    }
    new_entities: list[WLEDMainLight | WLEDSegmentLight] = []

    # More than 1 segment now? No main? Add main controls
    if not coordinator.keep_main_light and (
        len(current_ids) < 2 and len(segment_ids) > 1
    ):
        new_entities.append(WLEDMainLight(coordinator))

    # Process new segments, add them to Home Assistant
    for segment_id in segment_ids - current_ids:
        current_ids.add(segment_id)
        new_entities.append(WLEDSegmentLight(coordinator, segment_id))

    async_add_entities(new_entities)


def convert_K_to_RGB(colour_temperature):
    """
    Converts from K to RGB, algorithm courtesy of 
    http://www.tannerhelland.com/4435/convert-temperature-rgb-algorithm-code/
    """
    #range check
    if colour_temperature < 1000: 
        colour_temperature = 1000
    elif colour_temperature > 40000:
        colour_temperature = 40000
    
    tmp_internal = colour_temperature / 100.0
    
    # red 
    if tmp_internal <= 66:
        red = 255
    else:
        tmp_red = 329.698727446 * math.pow(tmp_internal - 60, -0.1332047592)
        if tmp_red < 0:
            red = 0
        elif tmp_red > 255:
            red = 255
        else:
            red = tmp_red
    
    # green
    if tmp_internal <=66:
        tmp_green = 99.4708025861 * math.log(tmp_internal) - 161.1195681661
        if tmp_green < 0:
            green = 0
        elif tmp_green > 255:
            green = 255
        else:
            green = tmp_green
    else:
        tmp_green = 288.1221695283 * math.pow(tmp_internal - 60, -0.0755148492)
        if tmp_green < 0:
            green = 0
        elif tmp_green > 255:
            green = 255
        else:
            green = tmp_green
    
    # blue
    if tmp_internal >=66:
        blue = 255
    elif tmp_internal <= 19:
        blue = 0
    else:
        tmp_blue = 138.5177312231 * math.log(tmp_internal - 10) - 305.0447927307
        if tmp_blue < 0:
            blue = 0
        elif tmp_blue > 255:
            blue = 255
        else:
            blue = tmp_blue
    
    return (round(red), round(green), round(blue))
