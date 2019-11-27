#!/usr/bin/python3

"""
ioptron_Mount.py: Implements an object, "AzMountPro" to control an Ioptron AZ
Mount Pro. The init method takes care of all set up so instantiating the class
is sufficient to get it set up and it is then ready for move commands (which
start with "Go_")

TODOs:
    - Log errors rather than just passing them out and leaving them to the user.
    - Fix the DST thing in Get_TimeInfo
    - Implement Set_Meridian
    - Make Set_TimeOffset take a wider diversity of inputs.
    - Understand and implement Set_UTCTime
    - Add an option somewhere to move only in alt or az (similar to Go_Delta but
    with absolute values)
"""

__author__ = "David Lowndes"
__email__ = "david@lownd.es"
__status__ = "Prototype"

import dateutil
import inspect
import logging
import math
import serial
import time

def ArcSec_To_Degrees(arcsec):
    return arcsec / 3600


def ArcSec_To_Radians(arcsec):
    return math.radians(arcsec / 3600)


def Degrees_To_Arcsec(degrees):
    return degrees * 3600


def Degrees_To_Radians(degrees):
    return math.radians(degrees)


def Radians_To_Arcsec(radians):
    return math.degrees(radians) * 3600


def Radians_To_Degrees(radians):
    return math.degrees(radians)


class AzMountPro:
    """
    Controls the Ioptron AZ Mount Pro.

    This basically just implements (most) of the commands from IOptron's pdf
    of the "RS232 Command Language" - it's unclear as to whether this mount
    is V2 or V3 of the command set, see the pdfs for exact descriptions of
    exact methods of operation.
    """

    def __init__(self, serialPort):
        """
        All port settings are in the datasheet
        Asueye are the mandatory set up commands
        """
        logging.basicConfig()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        self.mount = serial.Serial()
        self.mount.port = serialPort
        self.mount.baudrate = 9600
        self.mount.bytesize = serial.EIGHTBITS
        self.mount.parity = serial.PARITY_NONE
        self.mount.stopbits = serial.STOPBITS_ONE
        self.mount.xonxoff = False
        self.mount.timeout = 2

        self.mount.open()
        self.logger.debug(self.mount)

        # Give it time to open?
        time.sleep(0.1)

        # Initialization sequence:
        # In order to properly initialize the mount with your software, you
        # must issue the following commands when you establish your link:
        #    :V#
        #    :MountInfo#

        # This command is the first initialization command of iOptron series
        # products.
        version = self._SendMessage(":V#", "#")
        model = self._SendMessage(":MountInfo#", 4)
        self.logger.info({"Version": version, "Model": model})
        assert (version[:-1] == 'V1.00') and (model == '5035')
        print("az mount pro connected")

        firmware_Versions = self.Get_FirmwareVersion()
        self.logger.info(("firmware versions:", firmware_Versions))

        # Put the mount into a known state.
        self.Track(False)
        self.Set_MoveRate("max")
        self.Set_AltLimit(-89, "degrees")

        # Collect and print as much status info as possible on set up.
        status = self.Get_StatusInfo(readable=True)
        self.logger.info(("Status info:", status))

        time_Info = self.Get_TimeInfo()
        self.logger.info(("Time info:", time_Info))

        latlong = self.Get_LatLong()
        self.logger.info(("Lat/long", latlong))

        # Keep track of if there is a new Alt and Az defined that has not yet
        # been commanded.
        self.new_Alt = False
        self.new_Az = False

        self.Go_Home()

    def __del__(self):
        self.logger.info(f"Closing mount {self.mount.close()}")


    def _SendMessage(self, command, expected="#"):
        """
        Send a message to the Az Mount Pro.

        The "expected" argument gives an idea of what the response will look
        like. It should either be a character which ends the respose, or an
        integer which is how many bytes are in the response. (See the datasheet
        for which commands end with a "#" and which are just a set number of
        characters.
        """

        # If the command is bytes already, just send it, otherwise turn the
        # provided string into bytes.
        if type(command) == str:
            self.mount.write(command.encode("utf-8"))
        elif type(command) == bytes:
            self.mount.write(command)
        else:
            raise TypeError

        # Make sure the message gets sent I guess?
        self.mount.flush()

        if type(expected) == int:
            reply = self.mount.read(expected)
        elif type(expected) == bytes:
            reply = self.mount.read_until(expected)
        elif type(expected) == str:
            reply = self.mount.read_until(expected.encode("utf-8"))
        else:
            raise TypeError
        reply = reply.decode("utf-8")

        self.logger.debug(("Message:", {
                "caller": inspect.stack()[1].function,
                "command": str(command),
                "reply": reply}))
        return reply

    def Get_AltAz(self, units="degrees"):
        """
        Response: “sTTTTTTTTTTTTTTTTT#”
        This command include a sign and 17 digits, and gets altitude and
        azimuth.
        The sign and first 8 digits stands for current altitude.
        The last 9 digits stands for current azimuth.
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed

        altaz_Reply = self._SendMessage(":GAC#", "#")[:-1]
        alt = int(altaz_Reply[:9]) * 0.01
        az = int(altaz_Reply[9:]) * 0.01

        if units == "degrees":
            alt = ArcSec_To_Degrees(alt)
            az = ArcSec_To_Degrees(az)
        elif units == "radians":
            alt = ArcSec_To_Radians(alt)
            az = ArcSec_To_Radians(az)

        return alt, az

    def Get_AltLimit(self, units="degrees"):
        """
        From V3 of the command set.

        Response: “snn#”
        This command gets the altitude limit value. The response includes a
        sign and 2 digits. The altitude limit not only applies to tracking, but
        also applies to slewing. Moving by arrow buttons does not affect by
        this limit. Tracking will be stopped if you move or slew the mount to a
        position lower than the altitude limit.
        Note: Valid data range is [-89, +89]. The resolution is 1 degree.
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed

        limit_Reply = int(self._SendMessage(":GAL#", "#")[:3])

        if units == "radians":
            limit_Reply = math.radians(limit_Reply)
        elif units == "arcsec":
            limit_Reply *= 3600
        return limit_Reply

    def Get_FirmwareVersion(self):
        """
        Gets the date of the mainboard’s and the hand controller’s firmware.
        The first “YYMMDD” indicates the date of the mainboard’s firmware, the
        second “YYMMDD” indicates the date of the hand controller’s firmware.

        Gets the date of the RA motor board’s and the Dec motor board’s
        firmware. The first “YYMMDD” indicates the date of the RA motor
        board’s firmware, the second “YYMMDD” indicates the date of the Dec
        motor board’s firmware.
        """

        fw1 = self._SendMessage(":FW1#", "#")[:-1]
        fw2 = self._SendMessage(":FW2#", "#")[:-1]
        mainboard = fw1[:6]
        handcontroller = fw1[6:]
        ra_motor = fw2[:6]
        dec_motor = fw2[6:]

        return {"mainboard": mainboard,
                "handcontroller": handcontroller,
                "ra_motor": ra_motor,
                "dec_motor": dec_motor}

    def Get_LatLong(self):
        """
        North and East are positive.
        """
        lat_Reply = self._SendMessage(":Gt#", "#")
        lat = ArcSec_To_Degrees(int(lat_Reply[1:-1]))

        long_Reply = self._SendMessage(":Gg#", "#")
        long = ArcSec_To_Degrees(int(long_Reply[1:-1]))

        return lat, long

    def Get_StatusInfo(self, readable = True):
        """
        From V2 of the command set. Gets general information about the state
        of the mount.
        """

        status = self._SendMessage(":GAS#", "#")

        gps_Msgs = {"0": "GPS off",
                    "1": "GPS on",
                    "2": "GPS working"}

        system_Msgs = {"0": "stopped (not zeroed)",
                       "1": "tracking (PEC disabled)",
                       "2": "slewing",
                       "3": "guiding",
                       "4": "meridian flipping",
                       "5": "tracking (PEC enabled",
                       "6": "parked",
                       "7": "stopped at home"}

        track_Msgs = {"0": "siderial",
                      "1": "lunar",
                      "2": "solar",
                      "3": "king",
                      "4": "custom"}

        speed_Msgs = {"1": "1x",
                      "2": "2x",
                      "3": "8x",
                      "4": "16x",
                      "5": "64x",
                      "6": "128x",
                      "7": "256x",
                      "8": "512x",
                      "9": "max"}

        time_Msgs = {"0": "unknown",
                     "1": "RS232",
                     "2": "Hand controller",
                     "3": "GPS"}

        hemi_Msgs = {"0": "Southern",
                     "1": "Northern"}

        if readable:
            gps = gps_Msgs[status[0]]
            system = system_Msgs[status[1]]
            track_Rate = track_Msgs[status[2]]
            move_Speed = speed_Msgs[status[3]]
            time_Source = time_Msgs[status[4]]
            hemisphere = hemi_Msgs[status[5]]

            return {"GPS status": gps,
                    "System status": system,
                    "Track rate": track_Rate,
                    "Move speed": move_Speed,
                    "Time source": time_Source,
                    "Hemisphere": hemisphere}
        else:
            # Just return the packed values instead.
            return status[:-1]

    def Get_TimeInfo(self):
        """
        Get the mount to return what time it thinks it is. Returns a lovely
        python datetime object. DST handling may be a bit wonky for now

        From V2 of the Command set
        This command include a sign and 16 digits, and gets time related data.
        The sign and first 3 digits stands for the minutes of UTC offset (time
        zone). Note: The Daylight Saving Time does not affect this offset.
        The 4th digit stands for the Daylight Saving Time, 0 for Daylight
        Saving Time not observed, 1 for Daylight Saving Time observed.
        The 5th to 10th digits stands for local Date.
        The 11th to 16th digits stands for local Time in 24 hours format.
        """

        time_Reply = self._SendMessage(":GLT#", "#")
        tz = time_Reply[0:4]

        dst = time_Reply[4]
        date = time_Reply[5:11]
        time_Stamp = time_Reply[11:17]

        # Is UTC offset in minutes a normal thing? ISO8601 may disagree
        utc_Hours = str(int(tz[1:])//60).rjust(2, "0")
        utc_Mins = str(int(tz[1:]) % 60).rjust(2, "0")

        # Jesus christ.
        dt_Objs = ["20", date[0:2], "-", date[2:4], "-", date[4:6], "T",
                   time_Stamp[0:2], ":", time_Stamp[2:4], ":", time_Stamp[4:6],
                   tz[0], utc_Hours, ":", utc_Mins]

        return dateutil.parser.parse("".join(dt_Objs)), dst

    def _Set_Altitude(self, altitude, units="degrees"):
        """
        Set an altitude to move to on the next move command. Resolution 0.01
        arcsec
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed
        self.logger.debug(f"Set altitude {altitude} {units}")

        if units == "degrees":
            altitude = Degrees_To_Arcsec(altitude)
        elif units == "radians":
            altitude = Radians_To_Arcsec(altitude)

        altitude = int(altitude / 0.01)

        #assert (altitude >= -32400000) and (altitude <= 32400000)

        if altitude >= 0:
            sign = "+"
        else:
            # sign is done already
            sign = ""

        # Send 8 digits
        cmd = ":Sa"+sign+str(altitude).rjust(8, "0")+"#"
        alt_Reply = int(self._SendMessage(cmd, 1))
        if alt_Reply == 1:
            self.new_Alt = True
        return alt_Reply

    def Set_AltLimit(self, limit, units="degrees"):
        """
        This command sets the altitude limit. The altitude limit not only
        applies to tracking, but also applies to slewing. Movement caused by
        arrow buttons does not affect by this limit. Tracking will be stopped
        if you move the mount to a position exceeds any limit.
        Note: Valid data range is [-89, +89]. The resolution is 1 degree.
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed

        if units == "radians":
            limit = Radians_To_Degrees(limit)
        elif units == "arcsec":
            limit = ArcSec_To_Degrees(limit)

        assert (limit >= -89) and (limit <= 89)

        if limit >= 0:
            sign = "+"
        else:
            sign = ""

        limit = str(int(limit)).rjust(2,"0")
        cmd = ":SAL"+sign+limit+"#"
        lim_Reply = int(self._SendMessage(cmd, 1))
        return lim_Reply

    def Set_AltAz(self, altitude, azimuth, units="degrees"):
        """
        Just calls _Set_Altitude and _Set_Azimuth one after another for
        convenience.
        """

        alt_Reply = self._Set_Altitude(altitude, units)
        az_Reply = self._Set_Azimuth(azimuth, units)

        return (alt_Reply, az_Reply)

    def _Set_Azimuth(self, azimuth, units="degrees"):
        """
        Set an azimuth to move to on the next move command. Resolution 0.01
        arcsec
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed
        self.logger.debug(f"Set azimuth {azimuth} {units}")

        if units == "degrees":
            azimuth = Degrees_To_Arcsec(azimuth)
        elif units == "radians":
            azimuth = Radians_To_Arcsec(azimuth)

        azimuth = int(azimuth / 0.01)

        assert (azimuth >= 0) and (azimuth <= 129600000)

        # Send 8 digits
        cmd = ":Sz"+str(azimuth).rjust(9, "0")+"#"
        az_Reply = int(self._SendMessage(cmd, 1))
        if az_Reply == 1:
            self.new_Az = True
        return az_Reply

    def Set_DST(self, is_DST):
        """
        Sets the status of Daylight Saving Time. “:SDS1#” means Daylight Saving
        Time observed, “:SDS0#” means Daylight Saving Time not observed.
        """
        if is_DST:
            cmd = ":SDS1#"
        else:
            cmd = ":SDS0"

        dst_Reply = int(self._SendMessage(cmd, 1))
        return dst_Reply

    def Set_Hemisphere(self, northern=True):
        if northern:
            cmd = ":SHE1#"
        else:
            cmd = ":SHE0#"
        hemi_Reply = int(self._SendMessage(cmd, 1))
        return hemi_Reply

    def Set_Latitude(self, latitude, units="degrees"):
        """
        North is positive. Resolutio 0.01 arcsec
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed

        if units == "degrees":
            latitude = Degrees_To_Arcsec(latitude)
        elif units == "radians":
            latitude = Radians_To_Arcsec(latitude)

        latitude = int(latitude / 0.01)

        assert (-32400000 < latitude) and (latitude < 32400000)

        if latitude >= 0:
            sign = "+"
        else:
            sign = ""

        # Send 8 digits
        cmd = ":SLA"+sign+str(latitude).rjust(8, "0")+"#"
        lat_Reply = int(self._SendMessage(cmd, 1))
        return lat_Reply

    def Set_Longitude(self, longitude, units="degrees"):
        """
        East is positive. Resolution 0.01 arcsec.
        """

        units_Allowed = ["arcsec", "degrees", "radians"]
        assert units in units_Allowed

        if units == "degrees":
            longitude = Degrees_To_Arcsec(longitude)
        elif units == "radians":
            longitude = Radians_To_Arcsec(longitude)

        longitude = int(longitude / 0.01)

        assert (-64800000 < longitude) and (longitude < 64800000)

        if longitude >= 0:
            sign = "+"
        else:
            sign = ""

        # Send 8 digits
        cmd = ":SLO"+sign+str(longitude).rjust(8, "0")+"#"
        long_Reply = int(self._SendMessage(cmd, 1))
        return long_Reply

    def Set_Meridian(self, option):
        """
        Command: “:SMTnnn#”
        Response: “1”
        This command will set the behavior about meridian treatment. The first
        digit 0 stands for stop at the position limit set below. The first
        digit 1 stands for flip at the position limit set below. The last 2
        digits stands for the position limit of degrees past meridian.
        """
        raise NotImplementedError

    def Set_MoveRate(self, rate="64x"):
        """
        This command sets the moving rate used for the N-S-E-W buttons. For n,
        specify an integer from 1 to 9. 1 stands for 1x sidereal tracking rate,
        2 stands for 2x, 3 stands for 8x, 4 stands for 16x, 5 stands for 64x, 6
        stands for 128x, 7 stands for 256x, 8 stands for 512x, 9 stands for
        maximum speed available.
        Note: 64x is assumed as a default by the next power up.
        """

        rates_Available = {
                "1x": "1",
                "2x": "2",
                "8x": "3",
                "16x": "4",
                "64x": "5",
                "128x": "6",
                "256x": "7",
                "512x": "8",
                "max": "9"
                }

        cmd = ":SR"+rates_Available[rate]+"#"
        rate_Reply = int(self._SendMessage(cmd, 1))
        return rate_Reply

    def Set_TimeOffset(self, minutes):
        """
        Sets the time zone offset from UTC (Daylight Saving Time will not
        affect this value). The offset can only be entered in the range of
        [-720, +780] minutes.
        """

        assert ((-720 <= minutes) and
                (minutes <= 780) and
                (type(minutes) == int))

        if minutes >= 0:
            sign = "+"
        else:
            sign = ""

        offset_Reply = int(self._SendMessage(":SG"+sign+str(minutes)+"#", 1))
        return offset_Reply

    def Set_TrackRate(self, mode="custom"):
        """
        These commands select the tracking rate. It selects sidereal (“:RT0#”),
        lunar (“:RT1#”), solar (“:RT2#”), King (“:RT3#”), or custom (“:RT4#”).
        This command has no effect on the slewing or moving by arrow buttons.
        Note: The sidereal rate is assumed as a default by the next power up.
        """
        modes_Available = {
                "siderial": "0",
                "lunar": "1",
                "solar": "2",
                "king": "3",
                "custom": "4"
                }
        cmd = ":RT"+modes_Available[mode]+"#"

        track_Reply = int(self._SendMessage(cmd, 1))
        return track_Reply

    def Set_UTCTime(self, datetime_Now):
        """
        From V3. Needs julian day with ms resolution.
            Command: “:SUTXXXXXXXXXXXXX#”
            Response: “1”
            This command sets the current UTC Time. The number equals (JD(current UTC Time) – J2000) *
            8.64e+7. Note: JD(current UTC time) means Julian Date of current UTC time. The resolution is 1
            millisecond.
        Or replace with (from V2):
            Command: “:SCYYMMDD#”
            Response: “1”
            Sets the current Local Date.
            Command: “:SLHHMMSS#”
            Response: “1”
            Sets the current Local Time. The time can only be entered in the
            range of 00:00:00 to 23:59:59.
        Probably only one of these works
        """
        raise NotImplementedError

    def Calibrate(self):
        """
        From V2.
        Calibrate mount (Sync). In equatorial mounts, the most recently defined
        right ascension and declination become the commanded right ascension
        and declination respectively. In Alt-Azi mounts, the most recently
        defined altitude and azimuth become the commanded altitude and azimuth.
        This command assumes that the mount has been manually positioned on the
        proper pier side for the calibration object. This command is ignored if
        slewing is in progress. This command should be used for initial
        calibration. It should not be used after the mount has been tracking
        unless it is known that it has not tracked across the meridian.
        """

        return int(self._SendMessage(":CM#", 1))

    def Go_AltAz(self, altitude, azimuth, units="degrees"):
        """
        Define an altitude and azimuth via Set_AltAz and then immediately send
        the move command to enact them.
        """

        set_Reply = self.Set_AltAz(altitude, azimuth, units)
        move_Reply = self._Move()

        return set_Reply, move_Reply

    def Go_Blocking(self, altitude, azimuth, units="degrees", poll_Time = 0.5):
        """
        Send the mount to a position and block until it reaches that position
        (or gets to within some small margin)
        """

        move_Reply = self.Go_AltAz(altitude, azimuth, units)

        while not (self.Is_At_AltAz(altitude, azimuth, 0.1) and (self.Is_Stopped)):
            time.sleep(poll_Time)

        return move_Reply


    def Go_Delta(self, altitude_Delta, azimuth_Delta, units="degrees"):
        """
        Get current alt/az, add the deltas and command to that position.
        """

        alt_Current, az_Current = self.Get_AltAz(units)

        new_Alt = alt_Current + altitude_Delta
        new_Az = az_Current + azimuth_Delta
        self.logger.debug({"init alt": alt_Current,
                           "init az": az_Current,
                           "new alt": new_Alt,
                           "new az": new_Az})

        return self.Go_AltAz(new_Alt, new_Az, units)

    def Go_Home(self):
        """
        This command will slew to the zero position (home position)
        immediately.
        """

        home_Reply = int(self._SendMessage(":MH#", 1))

        while not self.Is_Homed():
            time.sleep(0.5)

        return home_Reply

    def Is_At_AltAz(self, altitude_Query, azimuth_Query, margin=0, units="degrees"):
        altitude_Actual, azimuth_Actual = self.Get_AltAz(units)

#        alt_Good = (altitude_Actual == altitude_Query)
#        az_Good = (azimuth_Actual == azimuth_Query)
        self.logger.debug(f"Alt requested {altitude_Query}, alt measured {altitude_Actual}")
        self.logger.debug(f"Az requested {azimuth_Query}, az measured {azimuth_Actual}")

        alt_Good = (abs(altitude_Actual - altitude_Query) <= margin)
        az_Good = (abs(azimuth_Actual - azimuth_Query) <= margin)

        return (alt_Good and az_Good)

    def Is_Homed(self):
        """
        Is the mount slewing right now?
        """

        status = self.Get_StatusInfo(False)[1]
        self.logger.debug(status)
        if status == "7":
            return True
        else:
            return False

    def Is_Slewing(self):
        """
        Is the mount slewing right now?
        """

        status = self.Get_StatusInfo(False)[1]
        self.logger.debug(status)
        if status == "2":
            return True
        else:
            return False

    def Is_Stopped(self):
        """
        Is the mount stationary right now?
        """

        status = self.Get_StatusInfo(False)[1]
        self.logger.debug(status)
        if status in ["0", "6", "7"]:
            return True
        else:
            return False

    def Is_Tracking(self):
        """
        Is the mount doing some kind of tracking (eg siderial or whatever)
        """

        status = self.Get_StatusInfo(False)[1]
        self.logger.debug(status)
        if status in ["1", "5"]:
            return True
        else:
            return False

    def KeyPad(self, direction):
        """
        These commands have identical function as arrow key pressed. They will
        move mounts to N-E-S-W direction at specified speed (may change by
        “:SRn#”). The mount will keep moving until a “:qR#”, “:qD#”, and/or
        “:q#” sent.
        """

        directions = {"up": ":mn#",
                      "down": ":ms#",
                      "left": ":mw#",
                      "right": ":me#"}
        self._SendMessage(directions[direction], 0)

    def _Move(self):
        """
        From V2 of the command set.
        Slew to the most recently defined right ascension and declination
        coordinates or most recently defined altitude and azimuth coordinates
        (only works with Alt-Azi Mount). If the object is below the horizon,
        this will be stated, and no slewing will occur.
        """

        # Check alt and az have been set since last move
        assert self.new_Alt and self.new_Az
        # Clear flags
        self.new_Alt = False
        self.new_Az = False
        move_Reply = int(self._SendMessage(":MS#", 1))

        if not move_Reply:
            self.logger.warning("Mount says that the requested alt is below 0")

        return move_Reply

    def Reset_All(self):
        """
        This command will reset all settings to the default. Note: Time Zone
        and Date/Time will be persevered[sic].
        """

        return int(self._SendMessage(":RAS#", 1))

    def Stop(self):
        """
        This command will stop slewing only. Tracking and moving by arrow keys
        will not be affected.
        """

        return int(self._SendMessage(":Q#", 1))

    def Stop_Keypad(self):
        """
        This command will stop moving by arrow keys or “:mn#”, “:me#”, “:ms#”,
        “:mw#” command. Slewing and tracking will not be affected.
        """

        return int(self._SendMessage(":q#", 1))

    def Stop_LeftRight(self):
        """
        This command will stop moving by left and right arrow keys or “:me#”,
        “:mw#” command. Slewing and tracking will not be affected.
        """

        return int(self._SendMessage(":qR#", 1))

    def Stop_UpDown(self):
        """
        This commands will stop moving by up and down arrow keys or “:mn#”,
        “:ms#” command. Slewing and tracking will not be affected.
        """

        return int(self._SendMessage(":qD#", 1))

    def Track(self, do_Tracking):
        """
        Turn on/off the tracking function of the mount (siderial etc).
        """

        if do_Tracking:
            track_Reply = int(self._SendMessage(":ST1#", 1))
        else:
            track_Reply = int(self._SendMessage(":ST0#", 1))
        return track_Reply

def TestRun(mount):
    mount.Go_Home()
    while not mount.Is_Stopped():
        time.sleep(1)
    time.sleep(1)

    mount.Go_AltAz(80,190)
    while not mount.Is_Stopped():
        time.sleep(1)
    time.sleep(1)

    mount.Go_Delta(10,-10)
    while not mount.Is_Stopped():
        time.sleep(1)
    time.sleep(1)

if __name__ == "__main__":
    my_Mount = AzMountPro("COM3")

    #TestRun(my_Mount)
    my_Mount.Go_Blocking(80,190)