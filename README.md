🛠️Added support to version Core 12.5 (wled v0.20.2)
---
# Modded the official WLED integration with color temperature support (not CCT)

- Added color_temp control mode
- Changed effect list with preset list (so u can change preset on more-info page directly)
- Renamed integration name to 'wled-joy' so it can co-exist with the official one

## Installation
- Copy wled_joy folder to your custom_components folder
- Restart HA
- Add wled_joy from integration

## To work properly

- Lights should be deleted in the official component first
- Lights could only be added by auto-discovery(Bug, might fix later)
- Restart HA after modifying any files in the component

![](/pic0.jpg)
![](/pic1.jpg)
