#!/usr/bin/env python3

import logging
from logging import error, info

from typing import BinaryIO, Tuple, List, Optional


# File info so far
# 0x00000 - 0x00009  "succeeded", magic number
# 0x00200 - 0x0029F  Block of FF \  # first 16 blocks are scenes, rest are chase steps
# 0x002A0 - 0x002B7  Block of 00 /  Repeating until 0x5AAFF
# 0x5AB00 - 0x5AB53  Names of Channels / Pan / Tilt / Aux
# 0x5AB54 - 0x5AD53  DMX Channel assignment
# 0x5AD54 - 0x5AD83  Number of chase steps (16-bit) + 05 suffix
# 0x5AD84 - 0x5AD88  "acme\00"
# 0x5AD89 - 0x5AF88  Block of 02
# 0x5AF89 - 0x6A988  Chase step assignment (16-bit)
# 0x6A989 - 0x6A98C  Random stuff
# 0x6A98D - 0x6A99C  Virtual Dimmer enabled (per fixture)
# 0x6A99D - 0x6AA3C  Virtual Dimmer enabled (per fixture / channel)
# 0x6AA3D - 0x801FF  Block of FF, probably not used


class Chase:
    def __init__(self):
        self.step_ids: List[int] = [0] * LedCommanderParser.CHASE_STEP_COUNT
        self.step_count: int = 0

    def print(self):
        info(f" Chase: {'->'.join('%d' % i for i in self.step_ids[0:self.step_count])}")
        info(f" Number of steps: {self.step_count}")


class Scene:
    def __init__(self):
        self.fixture_channel_values: List[List[int]] = []
        self.fixture_channel_active: List[List[bool]] = []
        for fixture_id in range(LedCommanderParser.FIXTURES_COUNT):
            self.fixture_channel_values.append([0] * LedCommanderParser.CHANNELS_COUNT)
            self.fixture_channel_active.append([False] * LedCommanderParser.CHANNELS_COUNT)
        self.mystery_flags_1 = b"\x00" * 2
        self.number_of_values = 0
        self.mystery_flags_2 = b"\x00"

    def is_set(self):
        # return any(fixture != [255] * LedCommanderParser.CHANNELS_COUNT for fixture in self.fixture_channel_values)
        return any(any(fixture) for fixture in self.fixture_channel_active)

    def print(self):
        for fixture_id in range(LedCommanderParser.FIXTURES_COUNT):
            values = '|'.join('%02x' % self.fixture_channel_values[fixture_id][channel_id]
                              if self.fixture_channel_active[fixture_id][channel_id] else '  '
                              for channel_id in range(LedCommanderParser.CHANNELS_COUNT))
            info(f" Fixture {fixture_id + 1:02d}: [{values}]")

        info(f"Number of values: {self.number_of_values}")
        info(f"Mystery: {self.mystery_flags_1.hex()} {self.mystery_flags_2.hex()}")

    @classmethod
    def parse_from(cls, readfile: BinaryIO, *args, **kwargs) -> "Scene":
        self = cls(*args, **kwargs)
        for fixture_id in range(LedCommanderParser.FIXTURES_COUNT):
            self.fixture_channel_values[fixture_id] = [value for value in readfile.read(LedCommanderParser.CHANNELS_COUNT)]

        index = 0
        for byte in readfile.read(20):
            for bit_id in range(8):
                value = byte & (1 << bit_id) != 0
                fixture, channel = divmod(index, LedCommanderParser.CHANNELS_COUNT)
                self.fixture_channel_active[fixture][channel] = value
                index += 1

        self.mystery_flags_1 = readfile.read(2)  # Always b"\x01\x00" ?
        self.number_of_values = readfile.read(1)[0]
        self.mystery_flags_2 = readfile.read(1)  # Always b"\x00" ? Is "number_of_values" 16-bit?

        return self


class LedCommanderParser:
    FIXTURES_COUNT: int = 16
    CHASES_COUNT: int = 16
    CHANNELS_COUNT: int = 10
    CHANNELS_NAMES_COUNT: int = 12
    DMX_CHANNELS_COUNT: int = 512
    STATIC_SCENES_COUNT: int = 16
    CHASE_STEP_COUNT: int = 2000

    def __init__(self, file):
        self.channel_names = [b"unknown"] * self.CHANNELS_NAMES_COUNT
        self.default_channel_names = self._create_default_channel_names()
        self.dmx_assignments: List[Optional[Tuple[Optional[int], int]]] = [None] * self.DMX_CHANNELS_COUNT
        self.virtual_dimmer_modes: List[int] = [0] * self.FIXTURES_COUNT
        self.virtual_dimmer_assignments: List[List[int]] = []
        for fixture_id in range(LedCommanderParser.FIXTURES_COUNT):
            self.virtual_dimmer_assignments.append([0] * LedCommanderParser.CHANNELS_COUNT)
        self.static_scenes: List[Scene] = [Scene()] * self.STATIC_SCENES_COUNT
        self.chase_steps: List[Scene] = [Scene()] * self.CHASE_STEP_COUNT
        self.chases: List[Chase] = [Chase()] * self.CHASES_COUNT

        with open(file, "rb") as readfile:
            self.is_magic_number_ok = self._read_and_check_magic_number(readfile)
            if not self.is_magic_number_ok:
                error("Magic number missing! Abort")
                return
            info(f"Magic number ok")

            self._read_scenes(readfile)
            self._read_names(readfile)
            self._read_dmx_channel_assignments(readfile)
            self._read_chase_info(readfile)
            self.is_magic_number_ok = self._read_acme_info(readfile)
            if not self.is_magic_number_ok:
                error("ACME string missing! Abort")
                return

            self._read_mystery_dmx_info(readfile)
            self._read_chase_step_assignments(readfile)
            self._read_random_bytes(readfile)
            self._read_virtual_dimmer_modes(readfile)
            self._read_virtual_dimmer_assignments(readfile)
            self._read_rest(readfile)

    @staticmethod
    def _create_default_channel_names() -> List[str]:
        """
        Create a list of default channel names, incl. pan and tilt as well as aux 1 and aux 2
        :return: List of strings
        """
        return [f"Channel {i + 1}" for i in range(8)] + ["PAN", "TILT", "AUX 1", "AUX 2"]

    def _get_channel_name(self, channel_id: int) -> str:
        """
        Look up custom name of channel 1..10 or aux 1/aux 2. Return custom name for channel or default name if empty.

        :param channel_id: channel to get name for
        :return: string of channel name
        """
        if 0 <= channel_id < self.CHANNELS_NAMES_COUNT:
            custom_name: str = self._name_to_str(self.channel_names[channel_id])
            default_name: str = self.default_channel_names[channel_id]

            if custom_name:
                return f"{custom_name} ({default_name})"
            return default_name
        return "<invalid channel id>"

    @staticmethod
    def _read_and_check_magic_number(readfile: BinaryIO) -> bool:
        """
        Read first 512 bytes of file and check if magic string "succeeded" matches.

        First nine bytes contain "succeeded", rest should be \x00.
        Parses file offset 0x00000 - 0x00199

        :param readfile: file to read bytes from.
        :return: True if magic number was read correctly
        """
        magic_number = readfile.read(512)
        return magic_number == b"succeeded" + (b"\x00" * 503)

    def _read_scenes(self, readfile: BinaryIO) -> None:
        """
        Read blocks that contain information about scenes and chase steps.

        Parses file offset 0x00200 - 0x5AAFF

        :param readfile: file to read bytes from.
        """
        for static_scene_id in range(self.STATIC_SCENES_COUNT):
            scene = Scene.parse_from(readfile)
            if scene.is_set():
                info(f"Scene {static_scene_id + 1}:")
                scene.print()
            self.static_scenes[static_scene_id] = scene

        for block_id in range(self.CHASE_STEP_COUNT):
            scene = Scene.parse_from(readfile)
            if scene.is_set():
                info(f"Chase step {block_id + 1}:")
                scene.print()
            self.chase_steps[block_id] = scene

    @staticmethod
    def _read_name(readfile: BinaryIO) -> bytes:
        """
        Read name of channels, pan, tilt or aux buttons.

        :param readfile: file to read bytes from.
        :return read name in bytes
        """
        name_size: int = 7
        return readfile.read(name_size)

    @staticmethod
    def _name_to_str(raw_name: bytes) -> str:
        """
        Parse raw name into ascii string.

        :param raw_name: read raw name as seven bytes
        :return name as string
        """
        return raw_name.rstrip(b"\x00").decode("ascii", errors="replace")

    def _read_names(self, readfile: BinaryIO) -> None:
        """
        Read names of channels, pan, tilt and aux buttons.

        Parses file offset 0x5AB00 - 0x5AB53

        :param readfile: file to read bytes from.
        """
        for channel_id in range(self.CHANNELS_NAMES_COUNT):
            channel_name = self._read_name(readfile)
            self.channel_names[channel_id] = channel_name
            info(f"{self.default_channel_names[channel_id]} name: '{self._name_to_str(channel_name)}'")

    def _read_dmx_channel_assignments(self, readfile: BinaryIO) -> None:
        """
        Read assignments of DMX channels to Fixtures+Channel or Aux 1/Aux 2.

        Parses file offset 0x5AB54 - 0x5AD53

        :param readfile: file to read bytes from.
        """
        for dmx_channel in range(self.DMX_CHANNELS_COUNT):
            dmx_channel_assignment = readfile.read(1)[0]
            if dmx_channel_assignment == 0xA2:
                self.dmx_assignments[dmx_channel] = None
            elif dmx_channel_assignment == 0xA1:
                self.dmx_assignments[dmx_channel] = (None, 11)
                info(f"DMX Channel {dmx_channel + 1}: {self._get_channel_name(11)}")
            elif dmx_channel_assignment == 0xA0:
                self.dmx_assignments[dmx_channel] = (None, 10)
                info(f"DMX Channel {dmx_channel + 1}: {self._get_channel_name(10)}")
            else:
                dmx_fixture, dmx_channel_of_fixture = divmod(dmx_channel_assignment, self.CHANNELS_COUNT)
                self.dmx_assignments[dmx_channel] = (dmx_fixture, dmx_channel_of_fixture)
                info(f"DMX Channel {dmx_channel + 1}: "
                     f"Fixture {dmx_fixture + 1}: "
                     f"{self._get_channel_name(dmx_channel_of_fixture)}")

    def _read_chase_info(self, readfile: BinaryIO) -> None:
        """
        Read information about chases that aren't clear yet. Appears to be {n}\x05

        Parses file offset 0x5AD54 - 0x5AD83

        :param readfile: file to read bytes from.
        """
        for chase_id in range(self.CHASES_COUNT):

            number_of_steps_in_chase_raw = readfile.read(2)
            number_of_steps_in_chase = number_of_steps_in_chase_raw[0] + number_of_steps_in_chase_raw[1] * 256
            readfile.read(1)  # unknown, always b"\x05" ?

            info(f"Chase {chase_id} has {number_of_steps_in_chase} steps")

            chase = Chase()
            chase.step_count = number_of_steps_in_chase
            self.chases[chase_id] = chase

    @staticmethod
    def _read_acme_info(readfile: BinaryIO) -> bool:
        """
        Read acme string

        Parses file offset 0x5AD784 - 0x5AD88

        :param readfile: file to read bytes from.
        """
        return readfile.read(5) == b"acme\x00"

    def _read_mystery_dmx_info(self, readfile: BinaryIO) -> None:
        """
        Read information about DMX channels, perhaps. Appears to be always \x02

        Parses file offset 0x5AD89 - 0x5AF88

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.DMX_CHANNELS_COUNT):
            readfile.read(1)

    def _read_chase_step_assignments(self, readfile: BinaryIO) -> None:
        """
        Seem to be assignment of chase steps to scenes (?)

        Parses file offset 0x5AF89 - 0x6A988

        :param readfile: file to read bytes from.
        """
        for chase_id in range(self.CHASES_COUNT):
            for step in range(self.CHASE_STEP_COUNT):
                step_id = readfile.read(2)  # 16bit I guess
                step_id = step_id[0] + step_id[1] * 256
                self.chases[chase_id].step_ids[step] = step_id
            if self.chases[chase_id].step_count > 0:
                info(f"Chase {chase_id + 1}")
                self.chases[chase_id].print()

    @staticmethod
    def _read_random_bytes(readfile: BinaryIO) -> None:
        """
        Read bytes that couldn't be decoded yet.

        Parses file offset 0x6A989 - 0x6A98C

        :param readfile: file to read bytes from.
        """
        readfile.read(3)

    @staticmethod
    def _read_virtual_dimmer_mode(readfile: BinaryIO) -> int:
        """
        Read mode of virtual dimmer, off, RGB or RGBAUVW (Depending on FW>=1.5).

        TODO: Verify and create enum, for now 0 = disabled, else enabled

        :param readfile: file to read bytes from.
        """
        return readfile.read(1)[0]

    def _read_virtual_dimmer_modes(self, readfile: BinaryIO) -> None:
        """
        Read mode of virtual dimmer, off, RGB or RGBAUVW (Depending on FW>=1.5).

        Parses file offset 0x6A98D - 0x6A99C

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.FIXTURES_COUNT):
            self.virtual_dimmer_modes[fixture_id] = self._read_virtual_dimmer_mode(readfile)

    @staticmethod
    def _read_virtual_dimmer_assignment(readfile: BinaryIO) -> int:
        """
        Read flag whether to apply virtual dimmer onto channel of fixture.

        :param readfile: file to read bytes from.
        """
        return readfile.read(1)[0]

    def _read_virtual_dimmer_assignments(self, readfile: BinaryIO) -> None:
        """
        Read if virtual dimmer should be applied onto channel of fixture.

        Parses file offset 0x6A99D - 0x6AA3C

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.FIXTURES_COUNT):
            for channel_id in range(self.CHANNELS_COUNT):
                self.virtual_dimmer_assignments[fixture_id][channel_id] = self._read_virtual_dimmer_assignment(readfile)

    @staticmethod
    def _read_rest(readfile: BinaryIO) -> bool:
        """
        Read rest of file, which should consist only of 0xFF.

        Parses file offset 0x6AA3D - 0x801FF

        :param readfile: file to read bytes from.
        """
        rest = readfile.read()
        return rest == b"\xFF" * len(rest)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    lcp = LedCommanderParser(sys.argv[1])
