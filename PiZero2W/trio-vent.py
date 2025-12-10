#!/usr/bin/python
# -*- coding:utf-8 -*-
import os
import sys
import configparser
import pigpio
from paho.mqtt import client as mqtt_client
import json5
import time
import logging
from systemd import daemon
from systemd import journal

'''

 trio-vent  (c) 2025 Andreas Filsinger, GPL-Licence
 =========

 * control two adjustable exhaust fan motors by hardware PWM
 * control a third vent by a Shelly 1 relay
 * mime a MQTT-device (trio-vent-xxx) for Home Assistant or the GUI
 * let Home Assistant get control by several "command" topics
 * send the vent-Status (0..100) to MQTT Broker via "status/#"
 * read WC-Room-Humidity from a Shelly Temp & Humidity Device via MQTT
 * Read WC-Light-Switch Status ("SW") from a Shelly 1 via MQTT
 * Write Relay 0/1 ("O") to a Shelly 1 via MQTT to control the third vent

 Logik:

 * wait to speed up WC Vent
 * short, long and longest stay triggers short/long motor overrun
 * a bit more fresh if WC exhaust runs
 * full power fresh air if the kitchen exhaust runs
 * run WC exhaust if humidity value is over adjustable value
 * ignore humdidity sensor if not changed in 50 minutes

 KEMO M240 (PWM Motor Controller, tow of them)
 =============================================
  PWM voltage: 3 V to 24 V
  PWM frequence: 100 Hz to 10 kHz

 Raspberry Pi Zero 2 W
 =====================
  pwmchip0
   GPIO 18, 3,3 V 1 kHz
   GPIO 13, 3,3 V 1 kHz

 Raspberry Pi 5 4 Gbyte
 ======================
  User GUI

 Raspberry Pi 400
 ================
  Home Assistant

'''

# this is trio-vent(ilator) version 
#
local_rev            = "185"                    # version / revision number

#
# To the mqtt-Broker this program (trio-vent) is like a Device
# subscribing-for and publish-this
#
local_device_id      = "trio-vent-106031846322" # software device

# R
status_zuluft    = "/status/vent-zuluft"      # 0..100
status_kueche    = "/status/vent-kueche"      # true | false
status_wc        = "/status/vent-wc"          # 0..100
status_version   = "/status/rev"              # Software Version String
 
# R/W
cmd_automatic = "/command/automatic"        # true | false
cmd_zuluft    = "/command/vent-zuluft"      # 0..100
cmd_kueche    = "/command/vent-kueche"      # true | false
cmd_wc        = "/command/vent-wc"          # 0..100

#
# a MQTT broker is a 24/7 message relay/örtöö for topics
# it is not a database, the broker duplicates messages for all subscribers
# you can ask if you get notified if a topic of your interest changes (="subscribe")
# you can send out a message (="publish") so all subscribers get it
# The Broker is not persistent so values from "missed" messages can not be retrieved
# But, if a Client publish a value with a "retain"-Flag the Broker hold the value for a time
# after the publish by system A, if a client system B is a bit late, in the moment subscribing 
# to a ratained message, system B gets the last known value stored by the broker. system B so 
# must not wait for the next value, wich maybe come in minutes or hours
#
mqtt_broker = "192.168.115.10"
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

#  Shelly 1 Mini Gen4 "Sensor WC-Light-Switch" and "Relay kitchen exhaust" in a single device
#
device_Shelly   = "shelly1minig4-ccba97c08c34"
#
topic_switch    = "/status/input:0"
#  sample Value = {"id":0,"state":false}
topic_relay    = "/command/switch:0"
#  sample Value = {"id":0, "source":"WS_in", "output":false,"temperature":{"tC":51.0, "tF":123.8}}
topic_ping     = "/command"
# possible Value = "announce"(fill announce) / "status_update"(send /status/*)


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
# time_OFF, ein Zeitzähler [Sekunden] von -TIME_AUTO_ON .. 0 .. cfg_nachlauf_lang
# 
# -x .. -1                      wartet auf Auto-ON
# 0 .. cfg_anlaufverzoegerung   verzögert den Anlauf
# 
#
time_OFF            = -TIME_AUTO_ON

#
# time_ON, ein Zeitzähler [Sekunden] der misst, wielange der Ventilator schon AN ist
#
time_ON             = 0

#
# time_LIGHT, ein Zeitzähler [Sekunden] der misst, wielange das Licht schon AN ist
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

#
# KÜCHE
#
KUECHE_Vent         = False     # False=kitchen vent stopped

#
# ZULUFT
#
ZULUFT_Vent_Percent = 25        # the daily default Speed in %
ZULUFT_Humidity     = float(0)  # the outdoor humidiy in %
ZULUFT_Temperature  = float(0)  # the outdoor temperature °C
ZULUFT_HT_Age       = 0         # age of the Humidity/Temperature Value in seconds, 0=values unset

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
PWM_FREQUENCY = 1000
PWM_PERCENT_FACTOR = 10000

# Zuluft PWM
PWM0_GPIO = 18

# WC Bad Abluft PWM
PWM1_GPIO = 13

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

# Global Vars "pi" and "client"
pi = pigpio.pi()
client = mqtt_client.Client(local_device_id)
client.username_pw_set(mqtt_user, mqtt_passwd)

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

# local "sleep()" used in this world
def trio_vent_sleep(seconds):
 while seconds>0:
  client.loop()
  time.sleep(1)
  seconds -= 1

def pwm_vent_Z(percent):
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 pi.hardware_PWM(PWM0_GPIO, PWM_FREQUENCY, percent*PWM_PERCENT_FACTOR)
 logger.info("MQTT" + CHAR_UP + " " + status_zuluft + " " + str(percent) + "% " + str( client.publish(local_device_id + status_zuluft, payload=percent, qos=1, retain=True)))
 
def pwm_vent_W(percent): 
 if percent>100:
  percent=100
 if percent<0:
  percent=0 
 pi.hardware_PWM(PWM1_GPIO, PWM_FREQUENCY, percent*PWM_PERCENT_FACTOR)
 logger.info("MQTT" + CHAR_UP + " " + status_wc + " " + str(percent) + "% " + str(client.publish(local_device_id + status_wc, payload=percent, qos=1, retain=True)))

def power_vent_K(onoff):
 if onoff:
  logger.info("MQTT" + CHAR_UP + " " + topic_relay + " on" + str(client.publish(device_Shelly + topic_relay, payload="on", qos=1))) 
  logger.info("MQTT" + CHAR_UP + " " + status_kueche + " true" + str(client.publish(local_device_id + status_kueche, payload="true", qos=1, retain=True)))
 else:
  logger.info("MQTT" + CHAR_UP + " " + topic_relay + " off" + str(client.publish(device_Shelly + topic_relay, payload="off", qos=1)))
  logger.info("MQTT" + CHAR_UP + " " + status_kueche + " false" + str(client.publish(local_device_id + status_kueche, payload="false", qos=1, retain=True)))

def mqtt_event_message(client, userdata, message):

    global KUECHE_Vent, WC_Light, time_OFF, nachlauf
    global WC_Humidity, WC_Temperature, WC_HT_Age
    global ZULUFT_Humidity, ZULUFT_Temperature, ZULUFT_HT_Age

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
    if message.topic == device_Shelly + topic_switch:
     WC_Light = json5.loads(message.payload.decode())["state"]
     logger.info(CHAR_DOWN + "MQTT WC-Licht " + str(WC_Light))

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
 
def mqtt_event_connect(client, userdata, flags, rc):
    logger.info("Connected with result code " + str(rc))

    # Remote Device Subscriptions
    logger.info("MQTT" + CHAR_LIKE + " " + topic_humidity + " " + str( client.subscribe(device_humidity_wc + topic_humidity, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_temperature + " " + str( client.subscribe(device_humidity_wc + topic_temperature, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_humidity + " " + str( client.subscribe(device_humidity_outdoor + topic_humidity, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_temperature + " " + str( client.subscribe(device_humidity_outdoor + topic_temperature, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + topic_switch + " " + str( client.subscribe(device_Shelly + topic_switch, qos=1)))

    # Own Subscriptions for me to serve
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_automatic + " " + str( client.subscribe(local_device_id + cmd_automatic, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_zuluft + " " + str( client.subscribe(local_device_id + cmd_zuluft, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_kueche + " " + str( client.subscribe(local_device_id + cmd_kueche, qos=1)))
    logger.info("MQTT" + CHAR_LIKE + " " + cmd_wc + " " + str( client.subscribe(local_device_id + cmd_wc, qos=1)))

def mqtt_event_disconnect(client, userdata, rc):
    logger.info("MQTT event disconnect, giving up")
    exit(1)

# Connect to the MQTT Server

logger.info("MQTT connect ...")
client.on_connect = mqtt_event_connect
client.on_message = mqtt_event_message
client.on_disconnect = mqtt_event_disconnect
client.connect(mqtt_broker, mqtt_port, keepalive=20)
trio_vent_sleep(3)

# Vent Motor Speed up

logger.info("Motor Init ...")
pwm_vent_Z(100)
pwm_vent_W(100)
trio_vent_sleep(6)

# Vent initial Setup

pwm_vent_Z(ZULUFT_Vent_Percent)
pwm_vent_W(0)

last_log   = ""
actual_log = ""

daemon.notify('READY=1')
client.publish(local_device_id + status_version, local_rev, qos=1, retain=True)
logger.info("trio-vent Rev. " + local_rev + " startup complete")

while True:

  # Clocks
  time_OFF += 1

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
   " K=" + str(KUECHE_Vent) +
   " h=" + str(ZULUFT_Humidity) + 
   " t=" + str(ZULUFT_Temperature)
  )

  if actual_log!=last_log:
   logger.info("OFF=" + str(time_OFF) + actual_log)
   last_log = actual_log

  if time_OFF==-1:
   # force echaust after long idle
   logger.info("Vent on after Idle")
   pwm_vent_W(100)
   time.sleep(4)
   pwm_vent_W(WC_Vent_Silent)
   WC_Vent = True
   nachlauf = cfg_nachlauf_lang
     
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
     