#!/usr/bin/python
# -*- coding:utf-8 -*-
import os
import sys
import configparser
from paho.mqtt import client as mqtt_client
import json5
import time
import logging
from systemd import daemon
from systemd import journal

'''

 trio-vent  (c) 2025-2026 Andreas Filsinger, GPL-Licence
 =========

 overview:

 * control three vents
   "Z" for fresh Air
   "W" for WC exhaust
   "K" for the kitchen exhaust
 * use "WC-Light" and "WC-Humidity" as input sensors
 
 
 pwm-use:
 
 * "Z" and "W" are connected to PWM motorcontrollers (2x KEMO M240)
 * the speed can be fine tuned
 * hardware PWM of a Raspberry Pi Zero 2W ist used



 * "K" is controlled by a Shelly 1
  control two adjustable exhaust fan motors by hardware PWM
 * control a third vent (ON|OFF) by a Shelly 1 relay

 Wi Fi Signal Strength:
 
 cat /proc/net/wireless


 sensors:
 
 * the WC-Light switch
 * the WC-Humidity
 
 

 high humidity:

 * check the WC-Light Switch for use-detection
 * check WC room humidity and outdoor humidity
 * check Shelly and System Temperature for Failure/Oberheat Detection
 * check Outdoor H

 mqtt use:
 
 * read Shelly Humidity/Temperature values
 * read Shelly relay status
 * write Shelly relay ON|OFF for Kitchen-Motor
 
 mqtt publications:

 * mime a MQTT-device (trio-vent-xxx) for Home Assistant integration or the GUI
 * let Home Assistant get control by several "command" topics
 * send the vent-Status (0..100) to MQTT Broker via "status/#"
 * read WC-Room-Humidity from a Shelly Temp & Humidity Device via MQTT
 * Read WC-Light-Switch Status ("SW") from a Shelly 1 via MQTT
 * Write Relay 0/1 ("O") to a Shelly 1 via MQTT to control the third vent

 logic:

 * wait motors to speed up at full power, than slow down to the speed wanted
 * short, long and longest stay triggers short/long motor overrun
 * (a bit more fresh air if WC exhaust runs)
 * full power fresh air if the kitchen exhaust runs
 * run WC exhaust if humidity value is over adjustable value
 * ignore humdidity sensor if not changed in 50 minutes
 * report the power meter value to log for diagnose

 KEMO M240 (PWM Motor Controller, tow of them)
 =============================================
  PWM voltage: 3 V to 24 V
  PWM frequence: 100 Hz to 10 kHz

 Raspberry Pi Zero 2 W
 =====================
  pwmchip0
   pwm0, GPIO 18, 3,3 V 1 kHz -> vent Zuluft (fresh air) 
   pwm1, GPIO 13, 3,3 V 1 kHz -> vent WC (exhaust)

 Raspberry Pi 5 4 Gbyte
 ======================
  User GUI as the control unit


'''

# this is trio-vent(ilator) version 
#
local_rev            = "191"                    # version / revision number

# this constant values are results of ./trio-vent -t , messurement of all power-consumers
# messurements goes to trio-vent.txt
# all values in W / all arrays go from 100 % Power down to 0 % Power
# a motor can not start at <20% of control-fade
# a motor may come to a complete stop at <10% of control-fade
#
POWER_IDLE = 3.7
POWER_K    = 43.2
POWER_W    = (46.6,46.6,46.6,46.7,46.7,46.7,46.7,46.7,46.7,46.9,46.9,46.9,46.9,46.9,46.9,47.2,47.2,47.2,47.2,47.2,47.2,47.0,47.0,47.0,47.0,47.0,47.0,46.9,46.9,46.9,46.9,46.9,46.9,46.1,46.1,46.1,46.1,46.1,46.1,46.1,46.1,46.1,46.1,46.1,46.1,46.3,46.3,46.3,46.3,46.3,46.3,46.3,46.3,46.3,46.3,46.3,46.3,46.9,46.9,46.9,46.9,46.9,47.8,48.3,48.3,48.3,48.3,48.3,50.5,51.2,52.1,26.7,24.9,23.7,22.0,20.6,19.2,17.7,15.9,14.8,13.3,12.0,10.7,9.5,8.4,8.4,7.3,5.9,4.9,4.2,3.6,2.6,2.6,2.0,1.6,1.6,0.8,0.6,0.2,0.2,0.2)
POWER_Z    = (41.8,41.8,41.8,41.8,41.8,41.8,41.7,41.7,41.7,41.7,41.7,41.7,41.6,41.6,41.6,41.6,41.6,41.6,41.6,41.6,41.6,41.6,41.6,41.8,41.8,41.8,41.8,41.8,41.8,41.4,41.4,41.4,41.4,41.4,41.4,41.4,40.5,39.9,39.9,39.9,39.9,39.9,38.5,37.6,37.6,37.6,37.6,35.4,35.0,35.0,33.7,33.1,33.1,33.1,31.5,31.2,31.2,31.2,34.5,35.7,36.5,36.6,36.6,38.5,39.1,40.0,31.6,24.9,23.8,23.8,23.8,22.3,21.0,21.0,19.6,19.6,17.8,16.7,15.6,14.5,13.5,12.2,11.2,10.1,8.7,7.7,7.1,5.8,5.3,4.3,4.3,3.1,2.5,2.0,1.5,1.2,1.0,1.0,0.2,-0.1,-0.1)

#
# To the mqtt-Broker this program (trio-vent) is like a Device
# subscribing-for and publish-this
#
local_device_id      = "trio-vent-106031846322" # software device

# R
status_online        = "/online"                  # 
status_version       = "/status/rev"              # Software Version String as a 3 digit number
status_zuluft        = "/status/vent-zuluft"      # 0..100
status_kueche        = "/status/vent-kueche"      # true | false
status_wc            = "/status/vent-wc"          # 0..100
status_power         = "/status/power"            # System Power Consumption in [W]
status_temperature   = "/status/temperature"      # System Internal Temp: Raspberry Pi CPU, Shelly Device

# R/W
cmd_automatic        = "/command/automatic"        # true | false
cmd_zuluft           = "/command/vent-zuluft"      # 0..100
cmd_kueche           = "/command/vent-kueche"      # true | false
cmd_wc               = "/command/vent-wc"          # 0..100

#
# a MQTT broker is a 24/7 message relay/örtöö for topics
# it is not a database, the broker duplicates messages for all subscribers
# you can ask if you get notified if a topic of your interest changes (="subscribe")
# you can send out a message (="publish") so all subscribers get it
# The Broker is not persistent so values from "missed" messages can not be retrieved
# But, if a Client publish a value with a "retain"-Flag the Broker hold the value for a time
# after the publish by system A, if a client system B is a bit late - still unconnected, in the moment subscribing 
# to a ratained message, system B gets the last known value stored by the broker. system B so 
# must not wait for the next value, wich maybe come in minutes or hours
#
mqtt_broker = "192.168.178.27"
mqtt_port   = 1883
mqtt_user   = "user"
mqtt_passwd = "user"

#  Shelly H&T G3 (messuring WC air hummidity)
#
device_humidity_wc   = "shellyhtg3-543204567354"
topic_humidity       = "/status/humidity:0"
#  sample Value = {"id": 0,"rh":60.3}

#  Shelly H&T G3 (messuring Outdoor hummidity)
#
device_humidity_outdoor = "shellyhtg3-e4b323306910"
topic_temperature       = "/status/temperature:0"
# sample Value = {"id": 0,"tC":10.0, "tF":50.1}

#  Shelly 1 Mini Gen4: (dual use) "Sensor WC-Light-Switch" and "Relay kitchen exhaust" in a single device
#
device_Shelly1   = "shelly1minig4-ccba97c08c34"
#
cmd_relay    = "/command/switch:0"        # on|off
#
topic_switch    = "/status/input:0"
#  sample Value = {"id":0,"state":false}
topic_relay     = "/status/switch:0"
#  sample Value = {"id":0, "source":"WS_in", "output":false,"temperature":{"tC":51.0, "tF":123.8}}
topic_ping     = "/command"
# to get actual values of a sleeping Shelly 1 device:
# /command/switch:0 := status_update  "führt zu einem Update des status/switch:0, also ich bekomme Infos wie das Relays steht"
# /command := status_update "führt zu einem update aller status meldungen auch input:0 also wie steht der Eingang "SW"
#

# possible Value = "announce"(fill announce) / (input:0|switch:0) "status_update"(send /status/*)

#  Shelly 1PM Mini Gen3: (dual use) "Power Meter for the 3 Motors" and "Relay to POWER-OFF or POWER-ON the whole System (Reset)"
#
device_1PM = "shelly1pmminig3-48f6ee8d12dc"
#
topic_power = "/status/switch:0"
# sample Value {"id":0, "source":"HTTP_in", "output":true, "apower":25.7, "voltage":239.7, "freq":50.2, "current":0.188, "aenergy":{"total":913.252,"by_minute":[404.631,404.631,404.631],"minute_ts":1782936360}, "ret_aenergy":{"total":0.000,"by_minute":[0.000,0.000,0.000],"minute_ts":1782936360},"temperature":{"tC":68.1, "tF":154.6}}
# Interessante Werte:
#  output true|false
#  apower n
#  temperature.tC
# selbst Reset via:
#  "/command/switch:0" := "on" | "off"
#

#
# [OPTIONAL]: ein Shelly 2PM, er könnte alle 4 Funktionen leisten (daher nur 1 Shelly nicht 2 Shellys nötig)
#  1) P: (PM1+PM2) Fail Detection
#  2) Relay 1: System Power
#  3) Relay 2: Vent Küche
#  4) SW1: WC Light
#

# Vent Wartezeiten
cfg_anlaufverzoegerung = 15      # [seconds]
cfg_kleinesgeschaeft   = 60      # [seconds]
cfg_duschen            = 60*3    # [seconds]

# Vent Laufzeiten
cfg_nachlauf_kurz      = 50      # [seconds]
cfg_nachlauf_lang      = 3*60    # [seconds]
cfg_nachlauf_duschen   = 5*60    # [seconds]

# Humidity Grenzen
cfg_entfeuchtung_an    = 70       # [%], "0" = unknown

# Persistent

TIME_AUTO_ON           = 24*60*60 # [seconds] Forced ON after n seconds of idle


#
# time_RUN, ein Zeitzähler [Sekunden] seit wann das Programm schon läuft
#
time_RUN            = 0

#
# time_OFF, ein Zeitzähler [Sekunden] seit wann der WC Ventilator aus ist
#
# von -TIME_AUTO_ON .. 0 .. cfg_nachlauf_lang
# 
# -x .. -1                      wartet auf Auto-ON
# 0 .. cfg_anlaufverzoegerung   verzögert den Anlauf
# 
#
time_OFF            = -TIME_AUTO_ON

#
# time_ON, ein Zeitzähler [Sekunden] der misst, wie lange der WC Ventilator schon AN ist
#
time_ON             = 0

#
# time_LIGHT, ein Zeitzähler [Sekunden] der misst, wie lange das Licht schon AN ist
#
time_LIGHT          = 0

#
# WC
#
WC_Light            = False     # False=Light off
WC_Vent             = False     # False=Vent stopped
WC_Vent_Normal      = 31        # % Speed of Vent
WC_Vent_Silent      = 23        # % Speed of Vent
WC_Humidity         = float(0)  # actual Humidity Value in %, 0=Unset
WC_Temperature      = float(0)  # actual Temperature Value in °C, 0=Unset
WC_HT_Age           = 0         # age of the Humidity/Temperature Value in seconds, 0=values unset
WC_Soll             = 0         # last known local regulator value for this vent

#
# KÜCHE
#
KUECHE_Vent         = False     # False=kitchen vent stopped  
KUECHE_Relay        = False     # False=Relay is OFF          is set by MQTT

#
# ZULUFT
#
ZULUFT_Vent_Percent = 28        # the daily default Speed in %
ZULUFT_Humidity     = float(0)  # the outdoor humidiy in %
ZULUFT_Temperature  = float(0)  # the outdoor temperature °C
ZULUFT_HT_Age       = 0         # age of the Humidity/Temperature Value in seconds, 0=values unset
ZULUFT_Soll         = 0         # last known local regualtor value for this vent

#
# SYSTEM 
#
SYSTEM_Power                     = 0         # System Power Consumption
SYSTEM_Power_Age                 = 0         # age of the Power Value 
SYSTEM_Power_Last                = 0         # last Reported Power Value +-2 W 
SYSTEM_Shelly_Temperature        = 0         # Temp of 1PM/2PM
SYSTEM_Shelly_Temperature_Last   = 0         # zuletzt übertragener Wert
SYSTEM_CPU_Temperature           = 0         # Temp of CPU
SYSTEM_CPU_Temperature_Last      = 0         # zuletzt übertragener Wert

#
# Wie lange soll der Lüfter noch nachlaufen
#
nachlauf           = -1

#
# MQTT Icons to make clear "subscribe"(Heart) "message"(Arrow Down) "publish"(Arrow Up)
#
CHAR_UP   = "\u2191"
CHAR_DOWN = "\u2193"
CHAR_LIKE = "\u2665"
 
# PWM globals
PWM_FREQUENCY = 1000.0

# Zuluft PWM 18
PWM0_GPIO = 18

# WC Bad Abluft PWM 13
PWM1_GPIO = 13

# writef to send commands to linux interoperability files
def writef(file, command):
  with open(file, 'w') as interop_file:
    interop_file.write(command)
  

# Calculate absolut Humidity in Air from relative humidity and Temperature assuming "normal" Pressure: returns weight Water in g/m³
#  (c) https://github.com/mcgibbon
def water_from_HT(Celcius, Humidity, Luftdruck=101325): # Water [g/m³]
  T = Celcius + 273.15
  Dichte = Luftdruck / (287.1 * T)
  es = 611.2 * math.exp(17.67 * (T - 273.15) / (T - 29.65))
  rvs = 0.622 * es / (Luftdruck - es)
  rv = Humidity / 100.0 * rvs
  qv = rv / (1 + rv)
  return qv * Dichte * 1000

def get_cpu_temp(): # [°C]
    # Open the system thermal zone file
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp_raw = f.read()
    
    # Convert the millidegrees string to a float in Celsius
    return float(temp_raw) / 1000.0

# local "sleep()" used in this world
def trio_vent_sleep(seconds):
 while seconds>0:
  client.loop()
  time.sleep(1)
  seconds -= 1

def pwm_vent_Z(percent):
 global ZULUFT_Soll
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 ZULUFT_Soll = percent 
 writef('/sys/class/pwm/pwmchip0/pwm0/duty_cycle', str(int(1000000000/PWM_FREQUENCY * percent / 100.0)))
 if percent==0:
  writef('/sys/class/pwm/pwmchip0/pwm0/enable', '0')
 else:
  writef('/sys/class/pwm/pwmchip0/pwm0/enable', '1') 
 logger.info("MQTT" + CHAR_UP + " " + status_zuluft + " " + str(percent) + "% " + str( client.publish(local_device_id + status_zuluft, payload=percent, qos=1, retain=True)))
 
def pwm_vent_W(percent): 
 global WC_Soll
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 WC_Soll = percent 
 writef('/sys/class/pwm/pwmchip0/pwm1/duty_cycle', str(int(1000000000/PWM_FREQUENCY * percent / 100.0)))
 if percent==0:
  writef('/sys/class/pwm/pwmchip0/pwm1/enable', '0')
 else:
  writef('/sys/class/pwm/pwmchip0/pwm1/enable', '1')
 logger.info("MQTT" + CHAR_UP + " " + status_wc + " " + str(percent) + "% " + str(client.publish(local_device_id + status_wc, payload=percent, qos=1, retain=True)))

def power_vent_K(onoff):
 if onoff:
  logger.info("MQTT" + CHAR_UP + " " + cmd_relay + " on" + str(client.publish(device_Shelly1 + cmd_relay, payload="on", qos=1))) 
  logger.info("MQTT" + CHAR_UP + " " + status_kueche + " true" + str(client.publish(local_device_id + status_kueche, payload="true", qos=1, retain=True)))
 else:
  logger.info("MQTT" + CHAR_UP + " " + cmd_relay + " off" + str(client.publish(device_Shelly1 + cmd_relay, payload="off", qos=1)))
  logger.info("MQTT" + CHAR_UP + " " + status_kueche + " false" + str(client.publish(local_device_id + status_kueche, payload="false", qos=1, retain=True)))

def power_forecast():
 r = POWER_IDLE
 if KUECHE_Vent:
  r += POWER_K
 r += POWER_W[100-WC_Soll] 
 r += POWER_Z[100-ZULUFT_Soll]
 return r
 
def mqtt_event_message(client, userdata, message):

    global KUECHE_Vent, KUECHE_Relay, WC_Light, time_OFF, nachlauf
    global WC_Humidity, WC_Temperature, WC_HT_Age
    global ZULUFT_Humidity, ZULUFT_Temperature, ZULUFT_HT_Age
    global SYSTEM_Power, SYSTEM_Power_Age
    global SYSTEM_Shelly_Temperature

    # command "Küche Vent" {"true"|"false"}
    if message.topic == local_device_id + cmd_kueche:
     # vent
     v = message.payload.decode()
     logger.info(CHAR_DOWN + "MQTT Küche Vent " + v)
     if v=="true":
      pwm_vent_Z(100)
      KUECHE_Vent = True
     else:
      pwm_vent_Z(ZULUFT_Vent_Percent)
      KUECHE_Vent = False
     power_vent_K(KUECHE_Vent)
     
    # command "Automatic" {"true"|"false"}
    if message.topic == local_device_id + cmd_automatic:
     v = message.payload.decode()
     logger.info(CHAR_DOWN + "MQTT Automatic " + v)
    
    # command "Zuluft Vent" {0..100}
    if message.topic == local_device_id + cmd_zuluft:
     v = int(message.payload.decode())
     logger.info(CHAR_DOWN + "MQTT ZULUFT Vent " + str(v))
     pwm_vent_Z(v)

    # command "WC Vent" {0..100} values 0|>0
    #   "0" terminate overrun
    #  >"0" initiate overrun similar to run after "a long idle time"
    #
    if message.topic == local_device_id + cmd_wc:
     v = int(message.payload.decode())
     logger.info(CHAR_DOWN + "MQTT WC Vent " + str(v))
     if v==0:
      nachlauf=1
     else: 
      time_OFF=-2
    
    # Status change "WC-Lichtschalter" {"true"|"false"}
    if message.topic == device_Shelly1 + topic_switch:
     WC_Light = json5.loads(message.payload.decode())["state"]
     logger.info(CHAR_DOWN + "MQTT WC-Licht " + str(WC_Light))

    # Status change "Relais" {"true"|"false"}
    if message.topic == device_Shelly1 + topic_relay:
     KUECHE_Relay = json5.loads(message.payload.decode())["output"]
     logger.info(CHAR_DOWN + "MQTT Relay " + str(KUECHE_Relay))

    # Change of WC "Humidity" {JSON} 
    if message.topic == device_humidity_wc + topic_humidity:
     payload = str(message.payload.decode("utf-8"))
     WC_Humidity = float(json5.loads(payload)["rh"])
     logger.info(CHAR_DOWN + "MQTT WC Humidity " + str(WC_Humidity) + "%")
     WC_HT_Age = 1

    # Change of WC "Temperature" {JSON} 
    if message.topic == device_humidity_wc + topic_temperature:
     payload = str(message.payload.decode("utf-8"))
     WC_Temperature = float(json5.loads(payload)["tC"])
     logger.info(CHAR_DOWN + "MQTT WC Temperature " + str(WC_Temperature) + "°C")
     WC_HT_Age = 1

    # Change of OUTDOOR "Humidity" {JSON} 
    if message.topic == device_humidity_outdoor + topic_humidity:
     payload = str(message.payload.decode("utf-8"))
     ZULUFT_Humidity = float(json5.loads(payload)["rh"])
     logger.info(CHAR_DOWN + "MQTT ZULUFT Humidity " + str(ZULUFT_Humidity) + "%")
     ZULUFT_HT_Age = 1
     
    # Change of OUTDOOR "Temperature" {JSON} 
    if message.topic == device_humidity_outdoor + topic_temperature:
     payload = str(message.payload.decode("utf-8"))
     ZULUFT_Temperature = float(json5.loads(payload)["tC"])
     logger.info(CHAR_DOWN + "MQTT ZULUFT Temperature " + str(ZULUFT_Temperature) + "°C")
     ZULUFT_HT_Age = 1
     
    # Change of System-Power / Shelly-Temp {JSON}
    if message.topic == device_1PM + topic_power:
     payload = str(message.payload.decode("utf-8"))
     SYSTEM_Power = float(json5.loads(payload)["apower"])
     SYSTEM_Shelly_Temperature = float(json5.loads(payload)["temperature"]["tC"])
     logger.info(CHAR_DOWN + "MQTT POWER " + str(SYSTEM_Power) + f" W (expected {power_forecast()} W)")
     logger.info(CHAR_DOWN + "MQTT Temp " + str(SYSTEM_Shelly_Temperature) + " °C")
     SYSTEM_Power_Age = 1
 
def mqtt_event_connect(client, userdata, flags, rc):
    logger.info("Connected with result code " + str(rc))

    # Remote Device Subscriptions
    logger.info("MQTT" + CHAR_LIKE + " " + topic_humidity + " " + str(client.subscribe(device_humidity_wc + topic_humidity, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_temperature + " " + str(client.subscribe(device_humidity_wc + topic_temperature, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_humidity + " " + str(client.subscribe(device_humidity_outdoor + topic_humidity, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_temperature + " " + str(client.subscribe(device_humidity_outdoor + topic_temperature, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_switch + " " + str(client.subscribe(device_Shelly1 + topic_switch, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_relay + " " + str(client.subscribe(device_Shelly1 + topic_relay, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_power + " " + str(client.subscribe(device_1PM + topic_power, qos=1)))

    # Own Subscriptions for me to serve
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_automatic + " " + str(client.subscribe(local_device_id + cmd_automatic, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_zuluft + " " + str(client.subscribe(local_device_id + cmd_zuluft, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_kueche + " " + str(client.subscribe(local_device_id + cmd_kueche, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_wc + " " + str(client.subscribe(local_device_id + cmd_wc, qos=1)))

def mqtt_event_disconnect(client, userdata, rc):
    logger.info("MQTT event disconnect, giving up")
    exit(1)

#----------
# M A I N 
#----------

# Logging Setup Systemd/Console
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
#
# are we running under systemd or console?
#
if os.getenv("INVOCATION_ID")==None:
 # we run at console
 console_handler = logging.StreamHandler(sys.stdout)
 logger.addHandler(console_handler)
 logger.info("Log goes to console")
else:
 # we run as a systemd-service
 journald_handler = journal.JournalHandler(SYSLOG_IDENTIFIER="trio-vent")
 logger.addHandler(journald_handler)
 logger.info("Log goes to journalctl")

# Activate the 2 Hardware PWM Channels
if not(os.path.isdir('/sys/class/pwm/pwmchip0/pwm0/')):
 writef('/sys/class/pwm/pwmchip0/export', '0')
if not(os.path.isdir('/sys/class/pwm/pwmchip0/pwm1/')):
 writef('/sys/class/pwm/pwmchip0/export', '1')

# Connect to the MQTT Server
#
logger.info("MQTT connect ...")
client = mqtt_client.Client(local_device_id)
client.username_pw_set(mqtt_user, mqtt_passwd)
client.will_set(local_device_id + status_online, payload="false", qos=1, retain=True)
client.on_connect = mqtt_event_connect
client.on_message = mqtt_event_message
client.on_disconnect = mqtt_event_disconnect
client.connect(mqtt_broker, mqtt_port, keepalive=20)
trio_vent_sleep(1)

# trigger Shelly1 MQTT status
logger.info("MQTT" + CHAR_UP + " Shelly 1 initial Values" + str( client.publish(device_Shelly1 + topic_ping, payload="status_update", qos=1)))
trio_vent_sleep(1)

# Vent Motor Speed up
#
logger.info("Motor Init ...")
writef('/sys/class/pwm/pwmchip0/pwm0/enable', '0')
writef('/sys/class/pwm/pwmchip0/pwm0/period', str(int(1000000000.0/PWM_FREQUENCY)))
writef('/sys/class/pwm/pwmchip0/pwm1/enable', '0')
writef('/sys/class/pwm/pwmchip0/pwm1/period', str(int(1000000000.0/PWM_FREQUENCY)))
pwm_vent_Z(100)
pwm_vent_W(100)
trio_vent_sleep(6)

# Vent initial Setup
#
pwm_vent_Z(ZULUFT_Vent_Percent)
pwm_vent_W(0)

last_log   = ""
actual_log = ""

daemon.notify('READY=1')
client.publish(local_device_id + status_version, local_rev, qos=1, retain=True)
client.publish(local_device_id + status_online, "true", qos=1, retain=True)
logger.info("trio-vent Rev. " + local_rev + " startup complete")

#
#
if len(sys.argv)>=2:
 if sys.argv[1]=="-t":
 
  logger.info("start in testpower-Mode")
  pwm_vent_Z(0)
  pwm_vent_W(0)
  power_vent_K(False)

  # messure idle power
  trio_vent_sleep(12)
  with open('/root/trio-vent.txt', 'w') as f:
    IDLE_Power = SYSTEM_Power
    f.write(f'POWER_IDLE = {IDLE_Power:.1f}\n')
 
    # messure K Vent
    power_vent_K(True)
    trio_vent_sleep(12)
    K_Power = SYSTEM_Power - IDLE_Power
    f.write(f'POWER_K = {K_Power:.1f}\n')
    power_vent_K(False)
    
    # messure WC Vent 0..100
    f.write('POWER_W = (');
    i = 100
    pwm_vent_W(i)
    trio_vent_sleep(10)
    while i>-1:
     pwm_vent_W(i)
     trio_vent_sleep(6)
     W_Power = SYSTEM_Power - IDLE_Power
     f.write(f'{W_Power:.1f}')
     if i!=0:
      f.write(',')
     i -= 1
    f.write(')\n')
 
    # messure Z Vent 0..100
    f.write('POWER_Z = (');
    i = 100
    pwm_vent_Z(i)
    trio_vent_sleep(10)
    while i>-1:
     pwm_vent_Z(i)
     trio_vent_sleep(6)
     Z_Power = SYSTEM_Power - IDLE_Power
     f.write(f'{Z_Power:.1f}')
     if i!=0:
      f.write(',')
     i -= 1
    f.write(')\n')
 
  exit(0)

# main loop
while True:

  # Clocks
  time_OFF += 1
  time_RUN += 1

  if WC_Vent:
   time_ON += 1

  if WC_Light: 
   time_LIGHT += 1
  else:
   if (time_LIGHT>0) and (time_LIGHT<=cfg_anlaufverzoegerung):
    time_LIGHT -= 1
   else:
    time_LIGHT = 0
    
  if WC_Vent and not WC_Light:
   nachlauf -= 1
   
  if WC_HT_Age>0:
   WC_HT_Age += 1

  if ZULUFT_HT_Age>0:
   ZULUFT_HT_Age += 1

  # Debug all Parameters, but not "time_OFF" it is too noisy
  actual_log = ( 
   " ON=" + str(time_ON) + 
   " LIGHT=" + str(time_LIGHT) + 
   " NL=" + str(nachlauf) +
   " L=" + str(WC_Light) + 
   " V=" + str(WC_Vent) +
   " H=" + str(WC_Humidity) +
   " T=" + str(WC_Temperature) +
   " K=" + str(KUECHE_Vent) + "," + str(KUECHE_Relay) +
   " h=" + str(ZULUFT_Humidity) + 
   " t=" + str(ZULUFT_Temperature)
  )

  if actual_log!=last_log:
   logger.info("OFF=" + str(time_OFF) + actual_log)
   last_log = actual_log
   
  if abs(SYSTEM_Power - SYSTEM_Power_Last)>1.9:
   SYSTEM_Power_Last = SYSTEM_Power
   logger.info("MQTT" + CHAR_UP + f" Power {SYSTEM_Power} W " + str(client.publish(local_device_id + status_power, str(int(SYSTEM_Power)), qos=1, retain=True)))

  if time_OFF==-1:
   # force echaust after long idle
   logger.info("Vent on after Idle")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)
   WC_Vent = True
   nachlauf = cfg_nachlauf_lang
   
  if time_RUN % 3 == 0:
   SYSTEM_CPU_Temperature = get_cpu_temp()
   if (SYSTEM_CPU_Temperature>0) and (SYSTEM_Shelly_Temperature>0):
    if (abs(SYSTEM_Shelly_Temperature-SYSTEM_Shelly_Temperature_Last)>1.9) or (abs(SYSTEM_CPU_Temperature-SYSTEM_CPU_Temperature_Last)>1.9):
     SYSTEM_Shelly_Temperature_Last = SYSTEM_Shelly_Temperature
     SYSTEM_CPU_Temperature_Last = SYSTEM_CPU_Temperature
     logger.info("MQTT" + CHAR_UP + f" CPU-Temperature {SYSTEM_CPU_Temperature:.1f} °C, Shelly-Temperature {SYSTEM_Shelly_Temperature:.1f} °C " + str(client.publish(local_device_id + status_temperature, f'{{"cpu":{SYSTEM_CPU_Temperature:.1f},"shelly":{SYSTEM_Shelly_Temperature:.1f}}}', qos=1, retain=True)))
    
  if time_LIGHT==cfg_anlaufverzoegerung:
   # Belüftung nun an
   if not WC_Vent:
    logger.info("Vent on after Light on")
    pwm_vent_W(100)
    time.sleep(4)
    pwm_vent_W(WC_Vent_Normal)
    pwm_vent_Z(ZULUFT_Vent_Percent+3)
    WC_Vent = True

   # Nachlauf setzen (aber nicht rücksetzen)
   nachlauf = max(cfg_nachlauf_kurz, nachlauf)
   
  if (WC_Humidity>=cfg_entfeuchtung_an) and not WC_Vent:
   logger.info("Vent on after high Humidity")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)   
   WC_Vent = True
   # Nachlauf setzen (aber nicht rücksetzen)
   nachlauf = max(cfg_nachlauf_duschen, nachlauf)
   
  if time_LIGHT==cfg_anlaufverzoegerung+cfg_kleinesgeschaeft:
   # Belüftung nun länger laufen lassen
   logger.info("a bit longer Stay")
   nachlauf = max(cfg_nachlauf_lang, nachlauf)

  if time_LIGHT==cfg_anlaufverzoegerung+cfg_duschen:
   # Belüftung nun ganz lang laufen lassen
   logger.info("a full long Stay")
   nachlauf = max(cfg_nachlauf_duschen, nachlauf)
  
  if nachlauf==0:
   logger.info("WC vent OFF")
   # Switch off WC Vent
   pwm_vent_W(0)   
   # Zuluft wieder normal
   pwm_vent_Z(ZULUFT_Vent_Percent)
   WC_Vent = False
   time_ON = 0
   time_OFF = -TIME_AUTO_ON
   nachlauf = -1
   
  if WC_HT_Age>60*50: # = 50 [Min]
   logger.info("no WC humidity-value refresh within 50 minutes -> unset value to 0")
   WC_HT_Age = 0
   WC_Humidity = float(0)
   WC_Temperature = float(0)

  if ZULUFT_HT_Age>60*50: # = 50 [Min]
   logger.info("no ZULUFT humidity-value refresh within 50 minutes -> unset value to 0")
   ZULUFT_HT_Age = 0
   ZULUFT_Humidity = float(0)
   ZULUFT_Temperature = float(0)
   
  daemon.notify("WATCHDOG=1") 
  trio_vent_sleep(1)
  if not client.is_connected():
   logger.info("MQTT is not connected, giving up")
   exit(1)
     