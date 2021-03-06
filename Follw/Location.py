import sys, logging, time, json, platform, subprocess, re, multiprocessing, urllib, socket

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# GPS Hardware
try:
  from gps import *
except ImportError:
  logger.warning("GPSd library not installed")

# Mac OS Core Location
if platform.system() == 'Darwin':
  try:
    multiprocessing.set_start_method('spawn')
    import CoreLocation
  except ImportError:
    logger.warning("CoreLocation library not installed")
else:
  logger.debug("Core Location not supported on {}".format(platform.system()))

# Windows Location Services
#ToDo Import the right library which supports Location Services
# if platform.system() == 'Windows':
#   try:
#     import LocationServices
#   except ImportError:
#     logger.warning("Location Services library not installed")
# else:
#   logger.debug("Location Services not supported on {}".format(platform.system()))

# WiFi Location Lookup
wifiLocationConfigs = {
  'yandex': {},
  'wigle': {},
  'mls': {},
  'gls': {}
  }

# IP Location Lookup
ipLocationConfigs = {
  'ip-api.com': { 'url': 'http://ip-api.com/json/?fields=49344', 'latitudeKey': 'lat', 'longitudeKey': 'lon', 'interval': 60/45},
  'ipapi.co': { 'url': 'https://ipapi.co/json/', 'latitudeKey': 'latitude', 'longitudeKey': 'longitude', 'interval': (60*60*24)/1000},
  'extreme-ip-lookup.com': { 'url': 'https://extreme-ip-lookup.com/json/', 'latitudeKey': 'lat', 'longitudeKey': 'lon', 'interval': (60*60*24*31)/10000},
  'ipwhois.io': { 'url': 'https://ipwhois.app/json/?objects=latitude,longitude', 'latitudeKey': 'latitude', 'longitudeKey': 'longitude', 'interval': (60*60*24*31)/10000},
  'geoplugin.net': {'url': 'http://www.geoplugin.net/json.gp', 'latitudeKey': 'geoplugin_latitude', 'longitudeKey': 'geoplugin_longitude', 'accuracyKey': 'geoplugin_locationAccuracyRadius', 'interval': (60*60*24)/100000}
  }

class Location:
  terminate = False
  online = True

  # GPS Hardware
  gpsd = None
  nGPSDevices = 0

  # Mac OS Core Location
  coreLocationManager = None

  # WiFi Location Lookup
  wifiLocationLookup = False
  wifiLocationProvider = 'yandex'
  wifiAPIKey = None
  previousBSSID = None

  # IP Location Lookup
  ipLocationLookup = False
  ipLocationProvider = 'ip-api.com'
  ipLocationConfig = None
  # We have to keep track of the interval of IP lookups since IP Location Providers have a rate limit
  lastIPLocationLookup = 0

  location = None
  timestamp = None
  method = None

  def stop(self, signum = None, frame = None):
    self.terminate = True

  def online(self, online = True):
    self.online = online

  def offline(self, offline = True):
    self.online = not online

  def getLocation(self):
    location = None

    location = self.getGPSLocation()
    if location:
      self.method="GPS"

    if not location:
      location = self.getCoreLocationLocation()
      if location:
        self.method="CoreLocation"

    if not location:
      location = self.getLocationServicesLocation()
      if location:
        self.method="LocationServices"

    if not location:
      location = self.getWiFiLocation()
      if location:
        self.method="WiFi"

    if not location:
      location = self.getIPLocation()
      if location:
        self.method="IP"

    if location:
      self.location = location
      self.timestamp = time.time()

    return location

  def getGPSLocation(self, timeout = 2):
    """ Get the location using the GPS daemon """
    if not self.gpsd and 'gps' in sys.modules:
      try:
        self.gpsd = gps(mode=WATCH_ENABLE|WATCH_NEWSTYLE)
      except ConnectionRefusedError as e:
        logger.warning("Can't connect to GPSd")

    if self.gpsd:
      _time = time.time()
      report = None
      while not self.terminate:
        if self.gpsd.waiting():
          # Get current location from GPS device
          report = self.gpsd.next()
          if report['class'] == 'DEVICES':
            self.nGPSDevices = len(report['devices'])
          elif report['class'] == 'DEVICE':
            if report['activated'] == 0:
              logger.warning("GPS device disconnected")
              self.nGPSDevices -= 1
            else:
              logger.info("GPS device connected")
              self.nGPSDevices += 1
          elif report['class'] == 'TPV':
            if report['mode'] in [0,1]:
              logger.warning("GPS has no fix")
            else:
              if 'lat' in report and 'lon' in report:
                location = [ report['lat'], report['lon'] ]

                # Accuracy
                if 'epx' in report and 'epy' in report:
                  location.append(max([ report['epx'], report['epy'] ]))
                else:
                  location.append(None)

                # Altitude
                if 'alt' in report:
                  location.append(report['alt'])
                else:
                  location.append(None)

                # Direction
                if 'track' in report:
                  location.append(report['track'])
                else:
                  location.append(None)

                # Speed
                if 'speed' in report:
                  location.append(report['speed'])
                else:
                  location.append(None)

                return location

              logger.warning("No latitude or longitude in GPS report, this should not happen")
              logger.warning(report)
          elif report['class'] == 'SKY':
            if 'satellites' not in report:
              logger.debug("No satellites in view")
              return None
            _time = time.time()
          elif report['class'] in ['VERSION', 'WATCH']:
            _time = time.time()
          else:
            logger.debug("Unsupported class {}".format(report['class']))
            logger.debug(report)
            _time = time.time()
        else:
          elapsedTime = time.time() - _time
          if self.nGPSDevices == 0:
            logger.warning("No GPS device connected")
            return None
          if elapsedTime > timeout:
            logger.warning("GPS did not return a location")
            logger.debug(elapsedTime)
            return None
          time.sleep(0.1)

    return None

  def getCoreLocationLocation(self):
    """ Get the location using the macOS Core Location API """
    if platform.system() == 'Darwin' and 'CoreLocation' in sys.modules:
      if not self.coreLocationManager:
        self.coreLocationManager = CoreLocation.CLLocationManager.alloc().init()
        self.coreLocationManager.delegate()
        self.coreLocationManager.startUpdatingLocation()

      location = self.coreLocationManager.location()
      if location:
        coord = location.coordinate()
        direction = location.course()
        if direction < 0:
          direction = None
        speed = location.speed()
        if speed < 0:
          speed = None
        return [coord.latitude, coord.longitude, location.horizontalAccuracy(), location.altitude(), direction, speed]

      logger.debug("Core Location could not find your location")
      return None

    return None

  def getLocationServicesLocation(self):
    """ Get the location using the Windows Location Services API """
    if platform.system() == 'Windows' and 'ToDo' in sys.modules:
      #ToDo
      logger.debug("Location Service not yet implemented")
      return None

    return None

  def getWiFiLocation(self):
    """ Get the location using the WiFi BSSID """

    if not self.online:
      return None

    if not self.wifiLocationLookup:
      return None

    bssid = None

    if platform.system() == 'Linux':
      # Get the default route interface
      output = subprocess.check_output(['ip', 'route', 'show', 'default']).decode()

      interfaces = re.findall("^default via [0-9.]* dev ([^ ]*)", output, re.MULTILINE)
      interfaces = list(set(interfaces))

      if len(interfaces) != 1:
        logger.warning("More than one default route interfaces detected")
        return None

      interface = interfaces[0]
      output = subprocess.check_output(['iwconfig', interface]).decode()
      ssids = re.findall("^.* ESSID:\"(.*)\".*$", output, re.MULTILINE)
      bssids = re.findall("^.* Access Point: ([0-9a-fA-F:]*).*$", output, re.MULTILINE)
      signals = re.findall("^.* Signal level=(-[0-9]*) dBm.*$", output, re.MULTILINE)

      if len(bssids) == 0:
        logger.warning("No AP BSSID detected")
        return None
      if len(bssids) > 1:
        logger.warning("More than one AP BSSID detected")

      if len(signals) == 0:
        logger.warning("No AP BSSID detected")
        return None
      if len(signals) > 1:
        logger.warning("More than one AP BSSID detected")

      ssid = ssids[0]
      bssid = bssids[0]
      signal = signals[0]
    elif platform.system() == 'Darwin':
      output = subprocess.check_output(['/System/Library/PrivateFrameworks/Apple80211.framework/Versions/A/Resources/airport', '-I']).decode()
      logger.debug(output)
      ssids = re.findall("^ *SSID: (.*)$", output, re.MULTILINE)
      bssids = re.findall("^ *BSSID: ([0-9a-fA-F:]+)$", output, re.MULTILINE)
      channels = re.findall("^ *channel: ([0-9,]+)$", output, re.MULTILINE)
      signals = re.findall("^ *agrCtlRSSI: (-[0-9]+)$", output, re.MULTILINE)
      noises = re.findall("^ *agrCtlNoise: (-[0-9]+)$", output, re.MULTILINE)

      if len(bssids) == 0:
        logger.warning("No AP BSSID detected")
        return None
      if len(bssids) > 1:
        logger.warning("More than one AP BSSID detected")

      ssid = ssids[0]
      bssid = re.compile('\\b([0-9a-fA-F])\\b').sub('0\\1', bssids[0])
      signal = signals[0]
      ap = {'bssid': bssid, 'ssid': ssids[0], 'channel': channels[0], 'signal': signals[0], 'noise': noises[0], 'age': 0}

      output = subprocess.check_output(['/System/Library/PrivateFrameworks/Apple80211.framework/Versions/A/Resources/airport', '-s', '-x'])
      import plistlib
      scanresults = plistlib.loads(output)
      aps = []
      for scanresult in scanresults:
        aps.append({'bssid': re.compile('\\b([0-9a-fA-F])\\b').sub('0\\1', scanresult['BSSID']),
          'ssid': scanresult['SSID_STR'],
          'channel': scanresult['CHANNEL'],
          'signal': scanresult['RSSI'],
          'noise': scanresult['NOISE'],
          'age': scanresult['AGE']})
      logger.debug(bssids)
    elif platform.system() == 'Windows':
      #ToDo
      logger.info("AP BSSID detection not yet implemented on Windows")
      return None

    if bssid and bssid != self.previousBSSID:
      location = None
      if self.wifiLocationProvider == 'yandex':
        _bssid = bssid.replace(':', '')
        url = "http://mobile.maps.yandex.net/cellid_location/?wifinetworks={}:{}".format(_bssid, signal)
        logger.debug(url)
        try:
          with urllib.request.urlopen(url, timeout=1) as response:
            data = response.read().decode(response.headers.get_content_charset(failobj = 'utf-8'))
            logger.debug(data)
            latitude = float(re.compile(" latitude=\"([0-9.]*)\".*", re.MULTILINE).search(response).group(1))
            longitude = float(re.compile(" longitude=\"([0-9.]*)\".*", re.MULTILINE).search(response).group(1))
            location = [latitude, longitude]
        except urllib.error.HTTPError as e:
          if e.code == 404:
            logger.warning("No location found for BSSID {}".format(bssid))
          else:
            logger.error(e.code)
            return None
        except urllib.error.URLError as e:
          logger.error(e)
          return None
        except socket.timeout as e:
          logger.error(e)
          return None
      elif self.wifiLocationProvider == 'wigle' and self.wifiAPIKey:
        url = "https://api.wigle.net/api/v2/network/detail?netid={}&type=wifi".format(bssid, signal)
        logger.debug(url)
        try:
          request = urllib.request.Request(url, headers={'Authorization': 'Basic ' + self.wifiAPIKey})
          with urllib.request.urlopen(request, timeout=1) as response:
            data = response.read().decode(response.headers.get_content_charset(failobj = 'utf-8'))
            logger.debug(data)
            data = json.loads(data)
            if data.get('success', False) and data.get('results')[0].get('ssid', None) == ssid:
              latitude = data.get('results')[0].get('locationData')[0].get('latitude')
              longitude = data.get('results')[0].get('locationData')[0].get('longitude')
              location = [latitude, longitude]
            else:
              logger.debug("No location found for BSSID {}".format(bssid))
        except urllib.error.HTTPError as e:
          logger.error(e.code)
          return None
        except urllib.error.URLError as e:
          logger.error(e)
          return None
      elif self.wifiLocationProvider in ['mls', 'gls'] and self.wifiAPIKey:
        data = {'wifiAccessPoints': []}
        for ap in aps:
          data['wifiAccessPoints'].append({'macAddress': ap['bssid'], 'age': ap['age'], 'channel': ap['channel'], 'signalStrength': ap['signal'], 'signalToNoiseRatio': ap['noise']})
        logger.debug(data)
        if self.wifiLocationProvider == 'mls':
          url = "https://location.services.mozilla.com/v1/geolocate?key={}".format(self.wifiAPIKey)
        elif self.wifiLocationProvider == 'gls':
          url = "https://www.googleapis.com/geolocation/v1/geolocate?key={}".format(self.wifiAPIKey)
        logger.debug(url)
        try:
          request = urllib.request.Request(url, headers={'Content-Type': 'application/json'})
          with urllib.request.urlopen(request, json.dumps(data).encode(), timeout=1) as response:
            data = response.read().decode(response.headers.get_content_charset(failobj = 'utf-8'))
            logger.debug(data)
            data = json.loads(data)
            if data.get('location', False) and data.get('accuracy', None):
              latitude = data.get('location').get('lat')
              longitude = data.get('location').get('lng')
              accuracy = data.get('accuracy')
              location = [latitude, longitude, accuracy]
            elif data.get('error', False):
              logger.error(data.get('message'))
            else:
              logger.debug("No location found for BSSID {}".format(bssid))
        except urllib.error.HTTPError as e:
          logger.error(e.code)
          return None
        except urllib.error.URLError as e:
          logger.error(e)
          return None

      self.previousBSSID = bssid
      return location

    return None

  def getIPLocation(self):
    """ Get the location using the external IP address """
    global ipLocationConfigs

    if not self.online:
      return None

    if not self.ipLocationLookup:
      return None

    if not self.ipLocationConfig:
      self.ipLocationConfig = ipLocationConfigs[self.ipLocationProvider]

    elapsedTime = time.time() - self.lastIPLocationLookup
    if elapsedTime > self.ipLocationConfig['interval']:
      try:
        with urllib.request.urlopen(self.ipLocationConfig['url'], timeout=1) as response:
          data = response.read().decode(response.headers.get_content_charset(failobj = 'utf-8'))
          logger.debug(data)
          data = json.loads(data)

          latitude = data.get(self.ipLocationConfig['latitudeKey'])
          longitude = data.get(self.ipLocationConfig['longitudeKey'])
          location = [latitude , longitude]
          accuracy = None
          if 'accuracyKey' in self.ipLocationConfig:
            accuracy = data.get(self.ipLocationConfig['accuracyKey'], None)
          if accuracy:
            location.append(accuracy)

          self.lastIPLocationLookup = time.time()

          return location
      except urllib.error.URLError as e:
        logger.error(e.reason)
      except urllib.error.HTTPError as e:
        logger.error(e.code)
      except socket.timeout as e:
        logger.error(e)

    return None