# About
With this project, I try to reverse engineer the saved files of the Stairville LED commander 16/2. It's an DMX hardware
to control up to 16 LED fixtures with both scenes and chases, but it's cumbersome to program.
As it supports to export and import all settings to an USB flash drive, we are able to reverse engineer the saved files
in order to program it on PC. 

# Current state
Files are always 524.800 bytes in size. Current firmware version is 1.5.

Findings so far:
* 0x00000 - 0x00009  "succeeded", magic number
* 0x00200 - 0x5AAFF  Block of scenes/chases? Each block is 0xB7 in size, containing values (0x9F) and flags (?)
* 0x5AB00 - 0x5AB53  Names of Channels / Pan / Tilt / Aux (fixed size, \0 padded)
* 0x5AB54 - 0x5AD53  DMX Channel assignment
* 0x5AD54 - 0x5AD83  Block of 00 00 05
* 0x5AD84 - 0x5AD88  "acme\00"
* 0x5AD89 - 0x5AF88  Block of 0x02
* 0x5AF89 - 0x6A988  Block of 0x00
* 0x6A989 - 0x6A98C  Random stuff
* 0x6A98D - 0x6A99C  Virtual Dimmer enabled (per fixture)
* 0x6A99D - 0x6AA3C  Virtual Dimmer enabled (per fixture / channel)
* 0x6AA3D - 0x801FF  Block of FF, probably not used
