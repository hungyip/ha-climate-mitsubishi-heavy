import asyncio
import logging
import binascii
import socket
import os.path
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

import time
from datetime import datetime
import sys
import math

from homeassistant.components.climate import (
    ClimateDevice, PLATFORM_SCHEMA,
    STATE_OFF, STATE_IDLE, STATE_HEAT, STATE_COOL, STATE_DRY, STATE_FAN_ONLY,
    STATE_AUTO, ATTR_OPERATION_MODE, SUPPORT_OPERATION_MODE,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_FAN_MODE, SUPPORT_SWING_MODE, 
	ATTR_SWING_MODE
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT, ATTR_TEMPERATURE, CONF_NAME,
    CONF_HOST, CONF_MAC, CONF_TIMEOUT, CONF_CUSTOMIZE)
from homeassistant.helpers.event import (async_track_state_change)
from homeassistant.core import callback
from homeassistant.helpers.restore_state import async_get_last_state
from configparser import ConfigParser
from base64 import b64encode, b64decode


# Definition of an HVAC Cmd Class Object
class HVAC_CMD:
    class __IR_SPEC:
        MITSUBISHI_HEAVY_HDR_MARK = 3200
        MITSUBISHI_HEAVY_HDR_SPACE = 1600
        MITSUBISHI_HEAVY_BIT_MARK = 400
        MITSUBISHI_HEAVY_ONE_SPACE = 1200
        MISTUBISHI_HEAVY_ZERO_SPACE = 400

    class HVAC_Power:
        Off = 0x08
        On = 0x00

    class HVAC_Mode:
        Auto = 0x07
        Cold = 0x06
        Dry = 0x05
        Hot = 0x03
        Fan = 0xD4
        Maint = 0x06

    class HVAC_Fan:
        Auto = 0xE0
        Low = 0xA0
        Mid = 0x80
        High = 0x60
        HiPower = 0x20
        Econo = 0x00

    class HVAC_VSwing:
        Swing = 0x0A
        Up = 0x02
        MUp = 0x18
        Middle = 0x10
        MDown = 0x08
        Down = 0x00
        Stop = 0x1A

    class HVAC_HSwing:
        Stop = 0xCC  # My Model don't have Horizontal Swing

    class HVAC_Clean:
        On = 0xDF
        Off = 0x20

    # Time Control is not added

    # BROADLINK_DURATION_CONVERSION_FACTOR
    # (Brodlink do not use exact duration in ħs but a factor of BDCF)
    __BDCF = 269/8192
    #           0     1     2     3     4     5      6     7     8     9     10
    __data = [0x52, 0xAE, 0xC3, 0x26, 0xD9, 0x11, 0x00, 0x07, 0x00, 0x00, 0x00]
    # BraodLink Sepecifc Headr for IR command start with a specific code
    __IR_BroadLink_Code = 0x26

    _log = True
    __StrHexCode = ""

    # Default Values for the Command
    Temp = 24
    Power = HVAC_Power
    Mode = HVAC_Mode
    Fan = HVAC_Fan
    VSwing = HVAC_VSwing
    HSwing = HVAC_HSwing
    Clean = HVAC_Clean

    def __init__(self):
        self.Power = self.HVAC_Power.Off
        self.Mode = self.HVAC_Mode.Auto
        self.Fan = self.HVAC_Fan.Auto
        self.VSwing = self.HVAC_VSwing.Stop
        self.HSwing = self.HVAC_HSwing.Stop
        self.Clean = self.HVAC_Clean.Off
        self._log = False

    def __val2BrCode(self, valeur, noZero=False):
        # val2BrCode: Transform a number to a broadlink Hex string
        valeur = int(math.ceil(valeur))  # force int, round up float if needed
        if (valeur < 256):
            # Working with just a byte
            myStr = "%0.2x" % valeur
        else:
            # Working with a Dword
            datalen = "%0.04x" % valeur
            if (noZero):
                myStr = datalen[2:4] + datalen[0:2]
            else:
                myStr = "00" + datalen[2:4] + datalen[0:2]
        return myStr
		
    def __val2BrLen(self, valeur):
        # val2BrLen: Transform a number to 3rd and 4th byte of broadlink code
        valeur = int(math.ceil(valeur))  # force int, round up float if needed
        
        # Working with just a byte
        datalen = "%0.4x" % valeur
        myStr = datalen[2:4] + datalen[0:2]
        return myStr

    def __build_cmd(self):
        # Build_Cmd: Build the Command applying all parameters defined.

        self.__data[5] |= self.HSwing | (self.VSwing & 0b00000010) | self.Clean
        self.__data[6] = ~self.__data[5]
        self.__data[7] |= self.Fan | (self.VSwing & 0b00011000)
        self.__data[8] = ~self.__data[7]
        self.__data[9] |= self.Mode | self.Power | self.Temp	
        self.__data[10] = ~self.__data[9]

        StrHexCode = ""
        for i in range(0, len(self.__data)):
            mask = 1
            tmp_StrCode = ""
            for j in range(0, 8):
                if self.__data[i] & mask != 0:
                    tmp_StrCode = tmp_StrCode + "%0.2x" % int(self.__IR_SPEC.MITSUBISHI_HEAVY_BIT_MARK*self.__BDCF) + "%0.2x" % int(self.__IR_SPEC.MITSUBISHI_HEAVY_ONE_SPACE*self.__BDCF)
                else:
                    tmp_StrCode = tmp_StrCode + "%0.2x" % int(self.__IR_SPEC.MITSUBISHI_HEAVY_BIT_MARK*self.__BDCF) + "%0.2x" % int(self.__IR_SPEC.MISTUBISHI_HEAVY_ZERO_SPACE*self.__BDCF)
                mask = mask << 1
            StrHexCode = StrHexCode + tmp_StrCode

        # StrHexCode contain the Frame for the HVAC Mitsubishi IR Command requested

        # Exemple using the no repeat function of the Command
        # Build the start of the BroadLink Command
        StrHexCodeBR = "%0.2x" % self.__IR_BroadLink_Code 	# First byte declare Cmd Type for BroadLink
        StrHexCodeBR = StrHexCodeBR + "%0.2x" % 0x00		# Second byte is the repeation number of the Cmd
        # Build Header Sequence Block of IR HVAC
        StrHeaderTrame = self.__val2BrCode(self.__IR_SPEC.MITSUBISHI_HEAVY_HDR_MARK * self.__BDCF)
        StrHeaderTrame = StrHeaderTrame + self.__val2BrCode(self.__IR_SPEC.MITSUBISHI_HEAVY_HDR_SPACE * self.__BDCF)
        # Build the Full frame for IR HVAC
        StrDataCode = StrHeaderTrame + StrHexCode
        # Calculate the lenght of the Cmd data and complete the Broadlink Command Header
		# Modified the line below with + 4 to work, I think the First 2 bytes and the last 2 bytes counts
        StrHexCodeBR = StrHexCodeBR + self.__val2BrLen(len(StrDataCode)/2 + 4)
        StrHexCodeBR = StrHexCodeBR + StrDataCode
        # Finalize the BroadLink Command ; must be end by 0x0d, 0x05 per protocol
        StrHexCodeBR = StrHexCodeBR + "0d05"
        # Voila, the full BroadLink Command is complete
        self.__StrHexCode = StrHexCodeBR

    def print_cmd(self):
        # Display to terminal the Built Command to be sent to the Broadlink IR Emitter
        self.__build_cmd()  # Request to build the Cmd
        print(self.__StrHexCode)  # Display the Command

    def get_cmd(self):  # it was send_cmd before which send out the command, now we would use this to get the cmd instead
        self.__build_cmd()
        myhex = self.__StrHexCode
        myhex = myhex.replace(' ', '').replace('\n', '')
        myhex = myhex.encode('ascii', 'strict')
        # device.send_data(binascii.unhexlify(myhex))
        return binascii.unhexlify(myhex)

# END HVAC_CMD

REQUIREMENTS = ['broadlink==0.9.0']

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE | SUPPORT_FAN_MODE | SUPPORT_SWING_MODE

# Config from configuration.yaml
# CONF_IRCODES_INI = 'ircodes_ini'
CONF_MIN_TEMP = 'min_temp'
CONF_MAX_TEMP = 'max_temp'
CONF_TARGET_TEMP = 'target_temp'
CONF_TARGET_TEMP_STEP = 'target_temp_step'
CONF_TEMP_SENSOR = 'temp_sensor'
CONF_OPERATIONS = 'operations'
CONF_FAN_MODES = 'fan_modes'
CONF_SWINGS = 'swings'
CONF_DEFAULT_SWING = 'default_swing'
CONF_DEFAULT_OPERATION = 'default_operation'
CONF_DEFAULT_FAN_MODE = 'default_fan_mode'

CONF_DEFAULT_OPERATION_FROM_IDLE = 'default_operation_from_idle'

DEFAULT_NAME = 'Broadlink IR MHI Climate'
DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 3
DEFAULT_MIN_TEMP = 18
DEFAULT_MAX_TEMP = 30
DEFAULT_TARGET_TEMP = 24
DEFAULT_TARGET_TEMP_STEP = 1
DEFAULT_OPERATION_LIST = [STATE_OFF, STATE_HEAT, STATE_COOL, STATE_DRY, STATE_FAN_ONLY, STATE_AUTO]
DEFAULT_FAN_MODE_LIST = ['auto', 'low', 'mid', 'high', 'hipower', 'econo']
DEFAULT_SWING_LIST = ['Swing', 'Up', 'M-Up', 'Middle', 'M-Down', 'Down', 'Manual']
DEFAULT_OPERATION = 'off'
DEFAULT_FAN_MODE = 'auto'
DEFAULT_SWING = 'Manual'

CUSTOMIZE_SCHEMA = vol.Schema({
    vol.Optional(CONF_OPERATIONS): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_FAN_MODES): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_SWINGS): vol.All(cv.ensure_list, [cv.string])
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MAC): cv.string,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): cv.positive_int,
    vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): cv.positive_int,
    vol.Optional(CONF_TARGET_TEMP, default=DEFAULT_TARGET_TEMP): cv.positive_int,
    vol.Optional(CONF_TARGET_TEMP_STEP, default=DEFAULT_TARGET_TEMP_STEP): cv.positive_int,
    vol.Optional(CONF_TEMP_SENSOR): cv.entity_id,
    vol.Optional(CONF_CUSTOMIZE, default={}): CUSTOMIZE_SCHEMA,
    vol.Optional(CONF_DEFAULT_OPERATION, default=DEFAULT_OPERATION): cv.string,
    vol.Optional(CONF_DEFAULT_FAN_MODE, default=DEFAULT_FAN_MODE): cv.string,
    vol.Optional(CONF_DEFAULT_SWING, default=DEFAULT_SWING): cv.string,
    vol.Optional(CONF_DEFAULT_OPERATION_FROM_IDLE): cv.string
})


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the Broadlink IR MHI Climate platform."""
    name = config.get(CONF_NAME)
    ip_addr = config.get(CONF_HOST)
    mac_addr = binascii.unhexlify(config.get(CONF_MAC).encode().replace(b':', b''))

    min_temp = config.get(CONF_MIN_TEMP)
    max_temp = config.get(CONF_MAX_TEMP)
    target_temp = config.get(CONF_TARGET_TEMP)
    target_temp_step = config.get(CONF_TARGET_TEMP_STEP)
    temp_sensor_entity_id = config.get(CONF_TEMP_SENSOR)
    operation_list = config.get(CONF_CUSTOMIZE).get(CONF_OPERATIONS, []) or DEFAULT_OPERATION_LIST
    fan_list = config.get(CONF_CUSTOMIZE).get(CONF_FAN_MODES, []) or DEFAULT_FAN_MODE_LIST
    swing_list = config.get(CONF_CUSTOMIZE).get(CONF_SWINGS, []) or DEFAULT_SWING_LIST
    default_operation = config.get(CONF_DEFAULT_OPERATION)
    default_fan_mode = config.get(CONF_DEFAULT_FAN_MODE)
    default_swing = config.get(CONF_DEFAULT_SWING)

    default_operation_from_idle = config.get(CONF_DEFAULT_OPERATION_FROM_IDLE)

    import broadlink

# connect to broadlink
    broadlink_device = broadlink.rm((ip_addr, 80), mac_addr, None)
    broadlink_device.timeout = config.get(CONF_TIMEOUT)

    try:
        broadlink_device.auth()
    except socket.timeout:
        _LOGGER.error("Failed to connect to Broadlink RM Device")
# finish connecting to broadlink

    async_add_devices([
        BroadlinkIRMHIClimate(hass, name, broadlink_device, min_temp, max_temp, target_temp, target_temp_step, temp_sensor_entity_id, operation_list, fan_list, swing_list, default_operation, default_fan_mode, default_swing, default_operation_from_idle)
    ])


class BroadlinkIRMHIClimate(ClimateDevice):

    def __init__(self, hass, name, broadlink_device, min_temp, max_temp, target_temp, target_temp_step, temp_sensor_entity_id, operation_list, fan_list, swing_list, default_operation, default_fan_mode, default_swing, default_operation_from_idle):

        """Initialize the Broadlink IR MHI Climate device."""
        self.hass = hass
        self._name = name

        self._min_temp = min_temp
        self._max_temp = max_temp
        self._target_temperature = target_temp
        self._target_temperature_step = target_temp_step
        self._unit_of_measurement = hass.config.units.temperature_unit

        self._current_temperature = 0
        self._temp_sensor_entity_id = temp_sensor_entity_id

        self._current_operation = default_operation
        self._current_fan_mode = default_fan_mode
        self._current_swing = default_swing

        self._operation_list = operation_list
        self._fan_list = fan_list
        self._swing_list = swing_list

        self._default_operation_from_idle = default_operation_from_idle

        self._broadlink_device = broadlink_device
        # self._commands_ini = ircodes_ini

        if temp_sensor_entity_id:
            async_track_state_change(
                hass, temp_sensor_entity_id, self._async_temp_sensor_changed)

            sensor_state = hass.states.get(temp_sensor_entity_id)

            if sensor_state:
                self._async_update_current_temp(sensor_state)

    def send_ir(self):

        # create new HVAC instance**
        MyHVAC = HVAC_CMD()

        section = self._current_operation.lower()

        if (section == 'off'):
            MyHVAC.Power = MyHVAC.HVAC_Power.Off
        else:
            MyHVAC.Power = MyHVAC.HVAC_Power.On


#  Need to check if climate have ON_OFF
#        if (if self._current_operation.lower() == 'maintanence' && section == 'off')
#        {
#            powerMode = MITSUBISHI_HEAVY_MODE_ON;
#            cleanMode = MITSUBISHI_HEAVY_ZMP_CLEAN_ON;
#        }

        if (section == 'auto'):
            MyHVAC.Mode = MyHVAC.HVAC_Mode.Auto
            MyHVAC.Temp = 0x80 - (0x10 * self._current_temperature)
        elif (section == 'cool'):
            MyHVAC.Mode = MyHVAC.HVAC_Mode.Cold
        elif (section == 'heat'):
            MyHVAC.Mode = MyHVAC.HVAC_Mode.Hot
        elif (section == 'dry'):
            MyHVAC.Mode = MyHVAC.HVAC_Mode.Dry
        elif (section == 'fan_only'):
            MyHVAC.Mode = MyHVAC.HVAC_Mode.Fan
            MyHVAC.Temp = 0
#        else (section == 'maint')
#      //Specify maintenance mode to activate clean mode
#            MYHVAC.Mode = MYHVAC.HVAC_Mode.Maint
        fanspeed = self._current_fan_mode.lower()
		
        if (fanspeed == 'auto'):
            MyHVAC.Fan = MyHVAC.HVAC_Fan.Auto
        elif (fanspeed == 'low'):
            MyHVAC.Fan = MyHVAC.HVAC_Fan.Low
        elif (fanspeed == 'med'):
            MyHVAC.Fan = MyHVAC.HVAC_Fan.Med
        elif (fanspeed == 'high'):
            MyHVAC.Fan = MyHVAC.HVAC_Fan.High
        elif (fanspeed == 'hipower'):
            MyHVAC.Fan = MyHVAC.HVAC_Fan.HiPower
        elif (fanspeed == 'econo'):
            MyHVAC.Fan = MyHVAC.HVAC_Fan.Econo

        temperature = int(self._target_temperature)
        if (temperature > 17 and temperature < 31):
            MyHVAC.Temp = (~((temperature - 17) << 4)) & 0xF0
        else:
            MyHVAC.Temp = (~((24 - 17) << 4)) & 0xF0

        vSwing = self.current_swing.lower()
        if (vSwing == 'manual'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.Stop
        elif (vSwing == 'swing'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.Swing
        elif (vSwing == 'up'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.Up
        elif (vSwing == 'm-up'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.MUp
        elif (vSwing == 'middle'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.Middle
        elif (vSwing == 'm-down'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.MDown
        elif (vSwing == 'down'):
            MyHVAC.VSwing = MyHVAC.HVAC_VSwing.Down

        for retry in range(DEFAULT_RETRY):
            try:
                payload = MyHVAC.get_cmd()
                self._broadlink_device.send_data(payload)
                break
            except (socket.timeout, ValueError):
                try:
                    self._broadlink_device.auth()
                except socket.timeout:
                    if retry == DEFAULT_RETRY-1:
                        _LOGGER.error("Failed to send packet to Broadlink RM Device")

    @asyncio.coroutine
    def _async_temp_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return

        self._async_update_current_temp(new_state)
        yield from self.async_update_ha_state()

    @callback
    def _async_update_current_temp(self, state):
        """Update thermostat with latest state from sensor."""
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        try:
            _state = state.state
            if self.represents_float(_state):
                self._current_temperature = self.hass.config.units.temperature(
                    float(_state), unit)
        except ValueError as ex:
            _LOGGER.error('Unable to update from sensor: %s', ex)

    def represents_float(self, s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def min_temp(self):
        """Return the polling state."""
        return self._min_temp

    @property
    def max_temp(self):
        """Return the polling state."""
        return self._max_temp

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._target_temperature_step

    @property
    def current_operation(self):
        """Return current operation ie. heat, cool, idle."""
        return self._current_operation

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return self._operation_list

    @property
    def current_swing(self):
        """Return current swing ie. up middle down etc."""
        return self._current_swing

    @property
    def swing_list(self):
        """Return the list of available swing modes."""
        return self._swing_list

    @property
    def current_fan_mode(self):
        """Return the fan setting."""
        return self._current_fan_mode

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        return self._fan_list

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    def set_temperature(self, **kwargs):
        """Set new target temperatures."""
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            self._target_temperature = kwargs.get(ATTR_TEMPERATURE)

            if not (self._current_operation.lower() == 'off' or self._current_operation.lower() == 'idle'):
                self.send_ir()
            elif self._default_operation_from_idle is not None:
                self.set_operation_mode(self._default_operation_from_idle)

            self.schedule_update_ha_state()

    def set_fan_mode(self, fan):
        """Set new target temperature."""
        self._current_fan_mode = fan

        if not (self._current_operation.lower() == 'off' or self._current_operation.lower() == 'idle'):
            self.send_ir()

        self.schedule_update_ha_state()

    def set_operation_mode(self, operation_mode):
        """Set new target Operation."""
        self._current_operation = operation_mode

        self.send_ir()
        self.schedule_update_ha_state()

    def set_swing_mode(self, swing_mode):
        """Set new target Swing."""
        self._current_swing = swing_mode
        self.send_ir()
        self.schedule_update_ha_state()

    @asyncio.coroutine
    def async_added_to_hass(self):
        state = yield from async_get_last_state(self.hass, self.entity_id)

        if state is not None:
            self._target_temperature = state.attributes['temperature']
            self._current_operation = state.attributes['operation_mode']
            self._current_fan_mode = state.attributes['fan_mode']
            self._current_swing = state.attributes['swing_mode']
