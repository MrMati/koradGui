import time

from koradserial import KoradSerial
from control import PowerSupplyCtrl

cands = KoradSerial.scan_devices(0x0416, 0x5011)

if not cands:
  print("No supported devices found")
  exit()

power_supply = KoradSerial(cands[0])
print("Model: ", power_supply.model)
print("Status: ", power_supply.status)

# power_supply.channels[0].voltage = 5
# power_supply.channels[0].current = 0.1
# power_supply.output.on()
#
try:
  while True:
    print(power_supply.channels[0].output_pair)
except KeyboardInterrupt:
  pass
finally:
  power_supply.close()


