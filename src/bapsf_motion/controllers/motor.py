"""
This program translates ASCII commands and sends messages to an Applied
Motion motor.  It can be used in drive.py for multidimensional movement.
"""
__all__ = ["MotorControl"]

import asyncio
import logging
import re
import socket
import threading
import time

from typing import Any, ClassVar, Dict, List, Optional

_logger = logging.getLogger(__name__)
_ipv4_pattern = re.compile("^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


class SimpleSignal:
    _handlers = None

    @property
    def handlers(self):
        if self._handlers is None:
            self._handlers = []
        return self._handlers

    def connect(self, func):
        if func not in self.handlers:
            self.handlers.append(func)

    def disconnect(self, func):
        try:
            self.handlers.remove(func)
        except ValueError:
            pass

    def emit(self, payload):
        for handler in self.handlers:
            handler(payload)


class Motor:
    name = ""
    logger = None
    base_heartrate = 2  # in seconds
    active_heartrate = 0.5  # in seconds
    _loop = None
    thread = None
    _config = {
        "ip": None,
        "manufacturer": "Applied Motion Products",
        "model": "STM23S-3EE",
        "gearing": None,  # steps/rev
        "encoder_resolution": None,  # counts/rev
    }  # type: Dict[str, Optional[str]]
    _settings = {
        "port": 7776,  # 7776 is Applied Motion's TCP port, 7775 is the UDP port
        "buffer_size": 1024,
        "socket": None,
        "max_connection_attempts": 3,
        "tasks": None,
    }  # type:  Dict[str, Optional[ClassVar[socket.socket], int]]
    _default_status = {
        "connected": False,
        "position": None,
        "alarm": None,
        "enabled": None,
        "fault": None,
        "moving": None,
        "homing": None,
        "jogging": None,
        "motion_in_progress": None,
        "in_position": None,
        "stopping": None,
        "waiting": None,
    }  # type: Dict[str, Any]
    _status = {**_default_status}
    status_changed = SimpleSignal()
    _commands = {
        "alarm": {
            "send": "AL",
            "recv": re.compile(r"AL=(?P<return>[0-9]{4})"),
        },
        "alarm_reset": {
            "senf": "AR",
            "recv": None,
        },
        "disable": {"send": "MD", "recv": None},
        "enable": {"send": "ME", "recv": None},
        "encoder_resolution": {
            "send": "ER",
            "recv": re.compile(r"ER=(?P<return>[0-9]+)"),
            "recv_processor": int,
        },
        "gearing": {
            "send": "EG",
            "recv": re.compile(r"EG=(?P<return>[0-9]+)"),
            "recv_processor": int,
        },
        "get_position": {
            "send": "IP",
            "recv": re.compile(r"IP=(?P<return>-?[0-9]+)"),
            "recv_processor": int,
        },
        "request_status": {
            "send": "RS",
            "recv": re.compile(r"RS=(?P<return>[ADEFHJMPRSTW]+)"),
        },
        "set_position": {
            "send": "DI{}",
            "send_processor": int,
            "recv": None,
        },
        "stop": {"send": "SK", "recv": None},
    }
    _commands["set_distance"] = _commands["set_position"]

    _alarm_codes = {
        1: "position limit [Drive Fault]",
        2: "CCW limit",
        4: "CW limit",
        8: "over temp  [Drive Fault]",
        10: "internal voltage [Drive Fault]",
        20: "over voltage [Drive Fault]",
        40: "under voltage",
        80: "over current [Drive Fault]",
        100: "open motor winding [Drive Fault]",
        400: "common error",
        800: "bad flash",
        1000: "no move",
        4000: "blank Q segment",
    }  # specific to STM motors

    # TODO: implement a "move_to" "FP" "feed to position"
    # TODO: implement a "jog_by" "FL" "feed to length"
    # TODO: implement a "soft_stop"
    # TODO: implement a setting of accel, deccel, speed
    # TODO: integrate hard limits
    # TODO: integrate soft limits
    # TODO: move off limit command
    # TODO: integrate homing
    # TODO: integrate zeroing
    # TODO: get motor firmware version, model numer, and sub-model using "MV"

    def __init__(
        self,
        *,
        ip: str,
        name: str = None,
        logger=None,
        loop=None,
        auto_start=False,
    ):
        self.setup_logger(logger, name)
        self.ip = ip
        self.connect()
        self._get_motor_parameters()
        self._configure_motor()
        self.retrieve_motor_status()
        self.setup_event_loop(loop, auto_start)

    def _configure_motor(self):
        self._send_raw_command("IFD")  # set format of immediate commands to decimal

    def _get_motor_parameters(self):
        self._config.update(
            {
                "gearing": self.send_command("gearing"),
                "encoder_resolution": self.send_command("encoder_resolution"),
            }
        )

    @property
    def status(self):
        return self._status

    # @property
    # def config(self):
    #     return self._config

    @property
    def ip(self):
        return self._config["ip"]

    @ip.setter
    def ip(self, value):
        if _ipv4_pattern.fullmatch(value) is None:
            raise ValueError(f"Supplied IP address ({value}) is not a valid IPv4.")

        self._config["ip"] = value

    @property
    def port(self):
        return self._settings["port"]

    @property
    def buffer_size(self):
        return self._settings["buffer_size"]

    @property
    def socket(self) -> socket.socket:
        return self._settings["socket"]

    @socket.setter
    def socket(self, value):
        if not isinstance(value, socket.socket):
            raise TypeError(f"Expected type {socket.socket}, got type {type(value)}.")

        self._settings["socket"] = value

    @property
    def tasks(self) -> List[asyncio.Task]:
        if self._settings["tasks"] is None:
            self._settings["tasks"] = []

        return self._settings["tasks"]

    @property
    def is_moving(self):
        is_moving = self.status["moving"]
        if is_moving is None:
            return False
        return is_moving

    @property
    def position(self):
        pos = self.send_command("get_position")
        self.update_status(position=pos)
        return pos

    def update_status(self, **values):
        old_status = self.status.copy()
        new_status = {**old_status, **values}
        changed = {}
        for key, value in new_status.items():
            if key not in old_status or (key in old_status and old_status[key] != value):
                changed[key] = value

        if changed:
            self.logger.debug(f"Motor status changed, new values are {changed}.")
            self.status_changed.emit(True)

        self._status = new_status

    def setup_logger(self, logger, name):
        log_name = _logger.name if logger is None else logger.name
        if name is not None:
            log_name += f".{name}"
            self.name = name
        self.logger = logging.getLogger(log_name)

    def setup_event_loop(self, loop, auto_start):
        # 1. loop is given and running
        #    - store loop
        #    - add tasks
        # 2. loop is given and not running
        #    - store loop
        #    - add tasks
        #    - start loop in separate thread if auto_start = True
        # 3. loop is NOT given
        #    - create new loop
        #    - store loop
        #    - add tasks
        #    - start loop in separate thread if auto_start = True
        # get a valid event loop
        if loop is None:
            loop = asyncio.new_event_loop()
        elif not isinstance(loop, asyncio.events.AbstractEventLoop):
            self.logger.warning(
                "Given asyncio event is not valid.  Creating a new event loop to use."
            )
            loop = asyncio.new_event_loop()
        self._loop = loop

        # populate loop with tasks
        task = self._loop.create_task(self._heartbeat())
        self.tasks.append(task)

        # auto-start event loop
        if auto_start:
            self.run()

    def connect(self):
        _allowed_attempts = self._settings["max_connection_attempts"]
        for _count in range(_allowed_attempts):
            try:
                msg = f"Connecting to {self.ip}:{self.port} ..."
                self.logger.debug(msg)

                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)  # 1 second timeout
                s.connect((self.ip, self.port))

                msg = "...SUCCESS!!!"
                self.logger.debug(msg)
                self.socket = s
                self.update_status(connected=True)
                return
            except (
                TimeoutError,
                InterruptedError,
                ConnectionRefusedError,
                socket.timeout,
            ) as error_:
                msg = f"...attempt {_count+1} of {_allowed_attempts} failed"
                if _count+1 < _allowed_attempts:
                    self.logger.warning(msg)
                else:
                    self.logger.error(msg)
                    raise error_

    def send_command(self, command, *args):
        msg = self._commands[command]["send"]
        value = ""
        if len(args):
            value = args[0]
        try:
            processor = self._commands[command]["send_processor"]
            value = processor(value)
        except KeyError:
            # If the "send_processor" key is not defined, then it is
            # assumed no processing needs to happen on the value to be
            # sent.
            pass
        msg = msg.format(value)

        # self.logger.debug(f"Sending command '{msg}'")
        recv_str = self._send_raw_command(msg)
        # self.logger.debug(f"Received message {recv_str}.")

        recv_pattern = self._commands[command]["recv"]
        if recv_pattern is not None:
            recv_str = recv_pattern.fullmatch(recv_str).group("return")

        try:
            processor = self._commands[command]["recv_processor"]
            return processor(recv_str)
        except KeyError:
            # If the "recv_processor" key is not defined, then it is
            # assumed the string is just to be passed back.
            return recv_str

    def _send_raw_command(self, cmd: str):
        cmd_str = bytearray([0, 7])  # header, equiv b'\x00\x07'
        cmd_str.extend(
            bytearray(cmd, encoding="ASCII")
        )  # command
        cmd_str.append(13)  # carriage return, end of command, equiv b'\r'

        try:
            self.socket.send(cmd_str)
        except ConnectionError:
            self.update_status(connected=False)
            self.connect()
            self.socket.send(cmd_str)

        # data = self.socket.recv(1024)
        # # print(
        # #     f"Sent command: {command} --  Received: {data.decode('ASCII')}",
        # # )
        # return data[2:-1].decode("ASCII")
        data = self.recv()
        return data.decode("ASCII")

    def recv(self):
        # all messages sent or received over TCP/UDP for Applied Motion Motors
        # use a byte header b'\x00\x07' and and end-of-message b'\r'
        # (carriage return)
        _header = b"\x00\x07"
        _eom = b"\r"  # end of message

        msg = b""
        while True:
            data = self.socket.recv(16)

            if not msg and _header in data:
                msg = data.split(_header)[1]
            else:
                msg += data

            if _eom in msg:
                msg = msg.split(_eom)[0]
                break

        return msg

    def retrieve_motor_status(self):
        cmd = "request_status"
        _rtn = self.send_command(cmd)

        # _status = {**self._default_status}
        _status = {
            "alarm": False,
            "enabled": False,
            "fault": False,
            "moving": False,
            "homing": False,
            "jogging": False,
            "motion_in_progress": False,
            "in_position": False,
            "stopping": False,
            "waiting": False,
        }  # null status

        for letter in _rtn:
            if letter == "A":
                _status["alarm"] = True
            elif letter in ("D", "R"):
                _status["enabled"] = True if letter == "R" else False
            elif letter == "E":
                _status["fault"] = True
            elif letter == "F":
                _status["moving"] = True
            elif letter == "H":
                _status["homing"] = True
            elif letter == "J":
                _status["jogging"] = True
            elif letter == "M":
                _status["motion_in_progress"] = True
            elif letter == "P":
                _status["in_position"] = True
            elif letter == "S":
                _status["stopping"] = True
            elif letter in ("T", "W"):
                _status["waiting"] = True

        pos = self.send_command("get_position")
        _status["position"] = pos

        if _status["alarm"]:
            _status["alarm_message"] = self.retrieve_motor_alarm(
                defer_status_update=True
            )

        self.update_status(**_status)

    def retrieve_motor_alarm(self, defer_status_update=False):
        rtn = self.send_command("alarm")

        codes = []
        for i, digit in enumerate(rtn):
            integer = int(digit)
            codes.append(integer * 10**(3-i))

        alarm_message = []
        for code in codes:
            try:
                msg = self._alarm_codes[code]
                alarm_message.append(f"{code:04d} - {msg}")
            except KeyError:
                pass

        alarm_message = " :: ".join(alarm_message)

        if alarm_message:
            self.logger.error(f"Motor returned alarm(s): {alarm_message}")

        if not defer_status_update and alarm_message:
            self.update_status(alarm_message=alarm_message)

        return alarm_message

    def enable(self):
        self.send_command("enable")
        self.retrieve_motor_status()

    def disable(self):
        self.send_command("disable")
        self.retrieve_motor_status()

    async def _heartbeat(self):
        while True:
            heartrate = self.active_heartrate if self.is_moving else self.base_heartrate

            self.retrieve_motor_status()
            # self.logger.debug("Beat status.")

            await asyncio.sleep(heartrate)

    def run(self):
        if self._loop.is_running():
            return

        self.thread = threading.Thread(target=self._loop.run_forever)
        self.thread.start()

    def stop_running(self):
        for task in list(self.tasks):
            task.cancel()
            self.tasks.remove(task)

        try:
            self.socket.close()
        except AttributeError:
            pass

        self._loop.call_soon_threadsafe(self._loop.stop)

    def stop(self):
        self.send_command("stop")


class MotorControl:

    MSIPA_CACHE_FN = "motor_server_ip_address_cache.tmp"
    MOTOR_SERVER_PORT = 7776
    BUF_SIZE = 1024
    # server_ip_addr = '10.10.10.10' # for direct ethernet connection to PC

    # - - - - - - - - - - - - - - - - -
    # To search IP address:
    last_pos = 999

    def __init__(self, server_ip_addr=None, msipa_cache_fn=None, verbose=True):
        self.verbose = verbose
        if msipa_cache_fn is None:
            self.msipa_cache_fn = self.MSIPA_CACHE_FN
        else:
            self.msipa_cache_fn = msipa_cache_fn

        # if we get an ip address argument, set that as the suggested server
        # IP address, otherwise look in cache file
        if server_ip_addr is not None:
            self.server_ip_addr = server_ip_addr
        else:
            try:
                # later: save the successfully determined motor server IP address
                #        in a file on disk
                # now: read the previously saved file as a first guess for the
                #      motor server IP address:
                self.server_ip_addr = None
                with open(self.msipa_cache_fn, "r") as f:
                    self.server_ip_addr = f.readline()
            except FileNotFoundError:
                self.server_ip_addr = None
        self.connect()

        # - - - - - - - - - - - - - - - - - - - - - - -
        if self.server_ip_addr is not None and len(self.server_ip_addr) > 0:
            try:
                print(
                    f"looking for motor server at {self.server_ip_addr}",
                    end=" ",
                    flush=True,
                )
                t = self.send_text("RS")
                print("status =", t[5:])
                if (
                    t is not None
                ):  # TODO: link different response to corresponding motor status
                    print("...found")
                    #                    self.reset_motor()
                    self.inhibit(inh=False)
                    self.send_text("IFD")  # set response format to decimal

                else:
                    print("motor server returned", t, sep="")
                    print("todo: why not the correct response?")

            except TimeoutError:
                print("...timed out")
            except (KeyboardInterrupt, SystemExit):
                print("...stop finding")
                raise

        with open(self.msipa_cache_fn, "w") as f:
            f.write(self.server_ip_addr)

        # TODO : encoder resolution for x/y and z motor is different, but cannot
        #        be changed through command ER
        # encoder_resolution = self.send_text('ER')
        # if float(encoder_resolution[5:]) != 4000:
        #    print('Encoder step/rev is not equal to motor step/rev. Check!!!')

    def __repr__(self):
        """return a printable version: not a useful function"""
        return f"{self.server_ip_addr}; {self.msipa_cache_fn}; {self.verbose}"

    def __str__(self):
        """return a string representation:"""
        return self.__repr__()

    def __bool__(self):
        """boolean test if valid - assumes valid if the server IP address is defined"""
        return self.server_ip_addr is not None

    def __enter__(self):
        """no special processing after __init__()"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """no special processing after __init__()"""

    def __del__(self):
        """no special processing after __init__()"""

    def connect(self, timeout: int = None):
        RETRIES = 30
        retry_count = 0
        while retry_count < RETRIES:  # Retries added 17-07-11
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                # if timeout is not None:
                #     # not on windows: socket.settimeout(timeout)
                #     s.setsockopt(
                #         socket.SOL_SOCKET,
                #         socket.SO_RCVTIMEO,
                #         struct.pack('LL', timeout, 0),
                #     )
                s.connect((self.server_ip_addr, self.MOTOR_SERVER_PORT))
                s.settimeout(6)
                self.s = s
                self.send_text("ZS6000")

                break
            except ConnectionRefusedError:
                retry_count += 1
                print(
                    f"...connection refused, at {time.ctime()}.  Is "
                    f"motor_server process running on remote machine?\n"
                    f"  Retry {retry_count}/{RETRIES} on {self.server_ip_addr}"
                )
            except TimeoutError:
                retry_count += 1
                print(
                    f"...connection attempt timed out, at {time.ctime()}\n",
                    f"  Retry {retry_count}/{RETRIES} on {self.server_ip_addr}.",
                )

        if retry_count >= RETRIES:
            input(
                " pausing in motor_control.py send_text() function, hit "
                "Enter to try again, or ^C: "
            )
            s.close()
            return self.connect(timeout)  # tail-recurse if retry is requested

    def send_text(self, text, timeout: int = None) -> str:
        """
        worker for below - opens a connection to send commands to the
        motor control server, closes when done
        """
        # note: timeout is not working - needs some MS specific
        #       iocontrol stuff (I think)

        s = self.s

        message = bytearray(text, encoding="ASCII")
        buf = bytearray(2)
        buf[0] = 0
        buf[1] = 7
        for i in range(len(message)):
            buf.append(message[i])
        buf.append(13)

        s.send(buf)

        BUF_SIZE = 1024
        data = s.recv(BUF_SIZE)
        return_text = data.decode("ASCII")
        return return_text

    def set_limits(self, cw, ccw):
        self.send_text(f"LP{cw}")
        self.send_text(f"LM{ccw}")

    def motor_velocity(self):

        resp = self.send_text("IV")
        rpm = float(resp[5:])
        return rpm

    def current_position(self):
        resp = self.send_text("IE")
        r = 0
        while r < 30:
            try:
                pos = float(resp[5:])
                self.last_pos = pos
                return pos

            except ValueError:
                time.sleep(2)
                r += 1

    def set_position(self, step):

        try:
            self.send_text(f"DI{step}")
            self.send_text("FP")
            time.sleep(0.1)

        except ConnectionResetError as err:
            print(f"*** connection to server failed: '{err.strerror}'")
            return False
        except ConnectionRefusedError as err:
            print(f"*** could not connect to server: '{err.strerror}'")
            return False
        except KeyboardInterrupt:
            print("\n______Halted due to Ctrl-C______")
            return False

        # TODO: see http://code.activestate.com/recipes/408859/  recv_end() code
        #       We need to include a terminating character for reliability,
        #       e.g.: text += '\n'

    def stop_now(self):
        self.send_text("SJ")
        self.send_text("SK")
        self.send_text("ST")

    def steps_per_rev(self, stepsperrev):
        self.send_text(f"EG{stepsperrev}")
        print(f"set steps/rev = {stepsperrev}\n")

    def set_zero(self):
        self.send_text("EP0")  # Set encoder position to zero
        resp = self.send_text("IE")
        if int(resp[5:]) == 0:
            print("Set encoder to zero\n")
            self.send_text("SP0")  # Set position to zero
            resp = self.send_text("IP")
            if int(resp[5:]) == 0:
                print("Set current position to zero\n")
            else:
                print("Fail to set current position to zero\n")
        else:
            print("Fail to set encoder to zero\n")

    def set_acceleration(self, acceleration):
        self.send_text(f"AC{acceleration}")

    def set_decceleration(self, decceleration):
        self.send_text(f"DE{decceleration}")

    def set_speed(self, speed):
        try:
            self.send_text(f"VE{speed}")
        except ConnectionResetError as err:
            print(f"*** connection to server failed: '{err.strerror}'")
            return False
        except ConnectionRefusedError as err:
            print(f"*** could not connect to server: '{err.strerror}'")
            return False
        except KeyboardInterrupt:
            print("\n______Halted due to Ctrl-C______")
            return False

    def check_status(self):
        # print("""
        #     # A = An Alarm code is present (use AL command to see code, AR command to clear code)
        #     # D = Disabled (the drive is disabled)
        #     # E = Drive Fault (drive must be reset by AR command to clear this fault)
        #     # F = Motor moving
        #     # H = Homing (SH in progress)
        #     # J = Jogging (CJ in progress)
        #     # M = Motion in progress (Feed & Jog Commands)
        #     # P = In position
        #     # R = Ready (Drive is enabled and ready)
        #     # S = Stopping a motion (ST or SK command executing)
        #     # T = Wait Time (WT command executing)
        #     # W = Wait Input (WI command executing)
        #     """)
        return self.send_text("RS")

    def set_jog_velocity(self, speed):
        self.send_text(f"JS{speed}")

    def commence_jogging(self):
        self.send_text("CJ")

    def stop_jogging(self):
        self.send_text("SJ")

    def set_jog_acceleration(self, acceleration):
        self.send_text(f"JA{acceleration}")
        self.send_text(f"JL{acceleration}")

    def reset_motor(self):

        self.send_text("RE", timeout=5)
        print("reset motor\n")

    def get_alarm_code(self):
        code = self.send_text("AL", timeout=5)

        if code == "0000":
            text = "No Alarms"

        elif code == "0001":
            text = "Alert! Position Limit"

        elif code == "0002":
            text = "Alert! CCW Limit"

        elif code == "0004":
            text = "Alert! CW Limit"

        elif code == "0008":
            text = "Alert! Over Heated"

        elif code == "0020":
            text = "Alert! Over Voltage"

        elif code == "0040":
            text = "Alert! Under Voltage"

        elif code == "0080":
            text = "Alert! Over Current"

        elif code == "1000":
            text = "Alert! No Move!"

        elif code == "4000":
            text = "Alert! Blank Q-Segment!"
        else:
            text = ""

        return text

    def clear_alarm(self):

        self.send_text("AR")
        print("Clear alarm. Check LED light to see if the fault condition persists.")

    def inhibit(self, inh=True):
        """
        Parameters
        ----------
        inh: bool
            If `True`, then raises the disable line on the PWM
            controller to disable the output.  If `False`, then lowers
            the inhibit liine.
        """
        if inh:
            cmd = "MD"
            print("motor disabled\n", sep="", end="", flush=True)
        else:
            cmd = "ME"
            print("motor enabled\n", sep="", end="", flush=True)

        try:
            self.send_text(cmd)  # INHIBIT or ENABLE

        except ConnectionResetError as err:
            print(f"*** connection to server failed: '{err.strerror}'")
            return False
        except ConnectionRefusedError as err:
            print(f"*** could not connect to server: '{err.strerror}'")
            return False
        except KeyboardInterrupt:
            print("\n______Halted due to Ctrl-C______")
            return False

        # TODO: see http://code.activestate.com/recipes/408859/  recv_end() code
        #       We need to include a terminating character for reliability,
        #       e.g.: text += '\n'
        return True

    def enable(self, en=True):
        """
        Parameters
        ----------
        en : bool
            If `True`, then lowers the inhibit line on the PWM
            controller to disable the output.  If `False`, then raises
            the inhibit line.
        """
        return self.inhibit(not en)

    def set_input_usage(self, usage):
        self.send_text(f"SI{usage}")
        print(f"set x3 input usage to SI {usage}\n")

    def close_connection(self):
        if self.s is not None:
            self.s.close()
            self.s = None

    def seek_home(self):
        self.send_text("SH")

    def go_to_zero(self):
        self.set_position(0)

    def get_status(self):
        code = self.send_text("SC")

        self.is_moving = True if code[2] == "1" else False

    def set_position_limit(self, steps):
        self.send_text(f"PL{steps}")

    def heartbeat(self):
        code = self.get_alarm_code()
        pos = self.current_position()
        vel = self.motor_velocity()
        self.get_status()
        return code, pos, vel, self.is_moving


if __name__ == "__main__":

    mc1 = MotorControl(verbose=True, server_ip_addr="192.168.0.40")
    mc1.set_position(0)
    mc1.current_position()
