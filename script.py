import os
path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
print(os.path.exists(path))   # deve dare True

import MetaTrader5 as mt5
mt5.shutdown()
res = mt5.initialize(path)
print(res, mt5.last_error())


 {
   "server": "VTMarkets-Demo",
   "login": 959911,
   "password": "Qpnldan1@1",
   "port": 443,
   "path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
 }