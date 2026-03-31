#!/usr/bin/env python3
import socket,threading,time,re,subprocess,sqlite3,math
import urllib.request,xml.etree.ElementTree as ET,json,os
from datetime import datetime,timezone
from PyQt6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFormLayout,QFrame,QSystemTrayIcon,QMenu,QInputDialog)
from PyQt6.QtCore import Qt,QTimer,pyqtSignal,QObject
from PyQt6.QtGui import QCursor,QIcon,QPixmap,QColor,QPainter
import sys
def load_config():
    config={}
    for path in [os.path.expanduser("~/.flex-to-log4om.conf"),os.path.expanduser("~/flex-to-log4om.conf")]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line=line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k,v=line.split("=",1);config[k.strip()]=v.strip()
            break
    return config
config=load_config()
FLEX_HOST="127.0.0.1";FLEX_PORT=4992;RIGCTLD_PORT=4532;ROTOR_UDP_PORT=12345
MY_CALLSIGN=config.get("MY_CALLSIGN","N4ASA")
MY_GRID=config.get("MY_GRID","FN43")
MY_LAT=float(config.get("MY_LAT","44.0"))
MY_LON=float(config.get("MY_LON","-70.0"))
MY_CITY=config.get("MY_CITY","")
MY_COUNTRY=config.get("MY_COUNTRY","United States")
DB_LOCAL=os.path.expanduser(config.get("DB_LOCAL","~/From QRZ 5-24-24.SQLite"))
RCLONE_PATH=config.get("RCLONE_PATH","/usr/bin/rclone")
GDRIVE_REMOTE=config.get("GDRIVE_REMOTE","Google_Drive:Log4OM/")
FLEX_IP=config.get("FLEX_IP","192.168.1.29")
PI_IP=config.get("PI_IP","")
QRZ_USERNAME=config.get("QRZ_USERNAME","")
QRZ_PASSWORD=config.get("QRZ_PASSWORD","")
WORKED_COLOR=config.get("WORKED_COLOR","#00FF7F")
current_freq=14100000;current_mode="USB"
freq_lock=threading.Lock();spot_db={};spot_lock=threading.Lock()
_spot_timer=None;_spot_timer_lock=threading.Lock();_dialog_open=False
_shown_freq=None;_shown_freq_lock=threading.Lock()

def schedule_spot_dialog(spots,freq_mhz,mode,dwell=2.0):
    global _spot_timer
    with _spot_timer_lock:
        if _spot_timer is not None:
            _spot_timer.cancel()
        t=threading.Timer(dwell,lambda: threading.Thread(target=pick_and_emit,args=(spots,freq_mhz,mode),daemon=True).start())
        t.daemon=True;t.start();_spot_timer=t
qrz_session=None;qrz_lock=threading.Lock()
MODE_MAP={"USB":"USB","LSB":"LSB","CW":"CW","CWR":"CW","AM":"AM","FM":"FM","DIGU":"USB","DIGL":"LSB","RTTY":"RTTY","SAM":"AM"}
BAND_MAP=[(1.8,2.0,"160m"),(3.5,4.0,"80m"),(5.3,5.5,"60m"),(7.0,7.3,"40m"),(10.1,10.15,"30m"),(14.0,14.35,"20m"),(18.068,18.168,"17m"),(21.0,21.45,"15m"),(24.89,24.99,"12m"),(28.0,29.7,"10m"),(50.0,54.0,"6m"),(144.0,148.0,"2m")]
QRZ_NS={'q':'http://xmldata.qrz.com'}
def freq_to_band(f):
    for lo,hi,b in BAND_MAP:
        if lo<=f<=hi: return b
    return "?"
def get_freq():
    with freq_lock: return current_freq
def get_mode():
    with freq_lock: return MODE_MAP.get(current_mode,current_mode)
def find_spots(freq_mhz,tol=0.005):
    """Return all spots within tolerance of freq_mhz, deduplicated by callsign"""
    matches=[]
    seen=set()
    with spot_lock:
        for sf,info in spot_db.items():
            if abs(sf-freq_mhz)<=tol:
                cs=info['callsign']
                if cs not in seen:
                    seen.add(cs)
                    matches.append((abs(sf-freq_mhz),sf,info))
    matches.sort(key=lambda x: x[0])
    return [(sf,info) for _,sf,info in matches]

def find_spot(freq_mhz,tol=0.005):
    matches=find_spots(freq_mhz,tol)
    return matches[0][1] if matches else None
def qrz_get_session():
    global qrz_session
    try:
        url=f"https://xmldata.qrz.com/xml/current/?username={QRZ_USERNAME}&password={QRZ_PASSWORD}&agent=FlexSpotsLinux"
        with urllib.request.urlopen(url,timeout=10) as r: root=ET.fromstring(r.read())
        key=root.find('.//q:Key',QRZ_NS)
        if key is not None:
            with qrz_lock: qrz_session=key.text
            print("QRZ session OK"); return key.text
    except Exception as e: print(f"QRZ session error: {e}")
    return None
def qrz_lookup(callsign):
    global qrz_session
    with qrz_lock: session=qrz_session
    if not session: session=qrz_get_session()
    if not session: return {}
    try:
        url=f"https://xmldata.qrz.com/xml/current/?s={session}&callsign={callsign}"
        with urllib.request.urlopen(url,timeout=10) as r: root=ET.fromstring(r.read())
        err=root.find('.//q:Error',QRZ_NS)
        if err is not None and 'session' in err.text.lower():
            session=qrz_get_session()
            if session: return qrz_lookup(callsign)
            return {}
        cs=root.find('.//q:Callsign',QRZ_NS)
        if cs is None: return {}
        return {child.tag.split('}')[1]:child.text for child in cs}
    except Exception as e: print(f"QRZ error: {e}"); return {}
solar_cache={};solar_cache_time=0
def get_solar_data():
    global solar_cache,solar_cache_time
    if time.time()-solar_cache_time<3600 and solar_cache: return solar_cache
    try:
        with urllib.request.urlopen("https://services.swpc.noaa.gov/products/summary/10cm-flux.json",timeout=10) as r: flux=float(json.loads(r.read()).get('Flux',0))
        with urllib.request.urlopen("https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json",timeout=10) as r: ssn=float(json.loads(r.read())[-1].get('ssn',0))
        solar_cache={'sunspots':ssn,'flux':flux};solar_cache_time=time.time()
        return solar_cache
    except: return {'sunspots':0,'flux':0}
WORKED_COLOR = "#00FF7F"  # Bright green for worked stations

def change_spot_color(callsign, color=WORKED_COLOR):
    """Find spot by callsign on Flex and change its color"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((FLEX_HOST, FLEX_PORT))
        s.settimeout(3)
        time.sleep(0.5)
        try: s.recv(65536)
        except: pass
        s.send(b'C1|client program FlexMonitor\n')
        time.sleep(0.2)
        s.send(b'C2|sub spot all\n')
        time.sleep(1.5)
        all_data = b""
        while True:
            try: all_data += s.recv(65536)
            except: break
        text = all_data.decode(errors='ignore')
        spot_ids = []
        for line in text.split('\n'):
            if f'callsign={callsign}' in line and 'removed' not in line:
                import re
                m = re.search(r'spot (\d+)', line)
                if m: spot_ids.append(m.group(1))
        for spot_id in spot_ids:
            cmd = f'C3|spot set {spot_id} color={color}\n'
            s.send(cmd.encode())
            time.sleep(0.1)
            print(f"✓ Changed spot {spot_id} ({callsign}) to {color}")
        s.close()
    except Exception as e:
        print(f"✗ Color change error: {e}")

def generate_qsoid():
    now=datetime.now(); return now.strftime("%Y%m%d%H%M%S")+str(now.microsecond)[:3]
def log_qso(callsign,freq_mhz,mode,rst_sent,rst_rcvd,notes=""):
    try:
        band=freq_to_band(freq_mhz);freq_khz=freq_mhz*1000
        qsoid=generate_qsoid()
        now_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        print(f"Looking up {callsign} on QRZ...")
        qrz=qrz_lookup(callsign)
        name=qrz.get('name_fmt',qrz.get('fname',''))
        country=qrz.get('country',qrz.get('land',''))
        dxcc=int(qrz.get('dxcc',0) or 0)
        cqzone=int(qrz.get('cqzone',0) or 0)
        ituzone=int(qrz.get('ituzone',0) or 0)
        gridsq=qrz.get('grid','')
        lat=float(qrz.get('lat',0) or 0)
        lon=float(qrz.get('lon',0) or 0)
        email=qrz.get('email','')
        qth=qrz.get('addr2','')
        solar=get_solar_data();sunspots=solar.get('sunspots',0)
        if name: print(f"  QRZ: {name}, {country}, CQ{cqzone}, Grid {gridsq}")
        else: print(f"  QRZ: no data for {callsign}")
        conn=sqlite3.connect(DB_LOCAL);cursor=conn.cursor()
        cursor.execute("""INSERT INTO Log (
            qsoid,callsign,band,mode,qsodate,qsoenddate,
            freq,freqrx,stationcallsign,rstsent,rstrcvd,comment,
            name,country,dxcc,cqzone,ituzone,
            gridsquare,lat,lon,mygridsquare,mylat,mylon,
            mycountry,mycity,email,qth,
            programid,programversion,qsorandom,Sunspots
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (qsoid,callsign.upper(),band,mode,now_utc,now_utc,
         freq_khz,freq_khz,MY_CALLSIGN,rst_sent,rst_rcvd,notes,
         name,country,dxcc,cqzone,ituzone,
         gridsq,lat,lon,MY_GRID,MY_LAT,MY_LON,
         MY_COUNTRY,MY_CITY,email,qth,
         "FlexSpotsLinux","1.0",1,sunspots))
        conn.commit();conn.close()
        print(f"✓ Logged: {callsign} {freq_mhz:.3f} MHz {mode} {band}")
        subprocess.Popen([RCLONE_PATH,"copy",DB_LOCAL,"Google_Drive:Log4OM/"])
        print("✓ Syncing to Google Drive...")
        threading.Thread(target=change_spot_color,args=(callsign,),daemon=True).start()
        return True
    except Exception as e:
        print(f"✗ Log error: {e}")
        import traceback;traceback.print_exc()
        return False
def calc_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def calc_distance_mi(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1; dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 3959 * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))

def grid_to_latlon(grid):
    """Convert Maidenhead grid square to (lat, lon) center point"""
    try:
        g=grid.upper().strip()
        if len(g)<4: return 0.0,0.0
        lon=(ord(g[0])-ord('A'))*20-180+int(g[2])*2
        lat=(ord(g[1])-ord('A'))*10-90+int(g[3])
        if len(g)>=6:
            lon+=(ord(g[4])-ord('A'))*(5/60)+(5/120)
            lat+=(ord(g[5])-ord('A'))*(2.5/60)+(2.5/120)
        else:
            lon+=1.0;lat+=0.5
        return lat,lon
    except Exception: return 0.0,0.0

def send_rotor_bearing(bearing):
    if not PI_IP: return
    try:
        msg = f"GOTO:{bearing:.1f}".encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, (PI_IP, ROTOR_UDP_PORT))
            s.sendto(msg, (PI_IP, 12346))
        print(f"  Rotor → {bearing:.0f}°")
    except Exception as e:
        print(f"  Rotor UDP error: {e}")

class QSODialog(QDialog):
    def __init__(self,callsign,freq_mhz,mode,qrz_data=None,parent=None):
        super().__init__(parent)
        self.callsign=callsign;self.freq_mhz=freq_mhz;self.mode=mode;self.qrz_data=qrz_data or {}
        lat=float(self.qrz_data.get('lat',0) or 0)
        lon=float(self.qrz_data.get('lon',0) or 0)
        self.bearing_source=""
        if lat and lon:
            self.bearing_source="qrz"
        else:
            grid=self.qrz_data.get('grid','')
            if grid:
                lat,lon=grid_to_latlon(grid)
                if lat and lon: self.bearing_source=f"grid:{grid[:4]}"
        if lat and lon:
            self.bearing=calc_bearing(MY_LAT,MY_LON,lat,lon)
            self.distance_mi=calc_distance_mi(MY_LAT,MY_LON,lat,lon)
        else:
            self.bearing=None;self.distance_mi=None
        self.setWindowTitle("Log QSO?")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Dialog)
        self.setMinimumWidth(400)
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.setup_ui()
    def setup_ui(self):
        self.setStyleSheet("""
            QDialog{background:#1e1e2e;color:#cdd6f4;font-family:'Courier New',monospace;cursor:default;}
            QLabel{color:#cdd6f4;font-size:13px;}
            QLabel#title{color:#a6e3a1;font-size:16px;font-weight:bold;}
            QLabel#callsign{color:#89b4fa;font-size:24px;font-weight:bold;}
            QLabel#freq{color:#f5c2e7;font-size:14px;}
            QLabel#info{color:#a6adc8;font-size:12px;}
            QLabel#bearing{color:#f9e2af;font-size:13px;font-weight:bold;}
            QPushButton#rotor{background:#f9e2af;color:#1e1e2e;border:none;border-radius:6px;padding:5px 16px;font-weight:bold;font-size:12px;}
            QPushButton#rotor:hover{background:#f5c543;}
            QLineEdit{background:#313244;border:1px solid #45475a;border-radius:6px;padding:6px 10px;color:#cdd6f4;font-size:13px;}
            QLineEdit:focus{border:1px solid #89b4fa;}
            QPushButton#log{background:#a6e3a1;color:#1e1e2e;border:none;border-radius:8px;padding:10px 24px;font-weight:bold;font-size:14px;}
            QPushButton#log:hover{background:#94e2d5;}
            QPushButton#cancel{background:#f38ba8;color:#1e1e2e;border:none;border-radius:8px;padding:10px 24px;font-weight:bold;font-size:14px;}
            QPushButton#cancel:hover{background:#eba0ac;}
            QFrame#divider{background:#45475a;}
        """)
        layout=QVBoxLayout(self);layout.setSpacing(10);layout.setContentsMargins(20,20,20,20)
        title=QLabel("📡 Log QSO?");title.setObjectName("title");title.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(title)
        self._div(layout)
        cs=QLabel(self.callsign);cs.setObjectName("callsign");cs.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(cs)
        fl=QLabel(f"{self.freq_mhz:.3f} MHz  •  {self.mode}  •  {freq_to_band(self.freq_mhz)}");fl.setObjectName("freq");fl.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(fl)
        name=self.qrz_data.get('name_fmt',self.qrz_data.get('fname',''))
        country=self.qrz_data.get('country','')
        grid=self.qrz_data.get('grid','')
        cqzone=self.qrz_data.get('cqzone','')
        if name or country:
            parts=[p for p in [name,country,f"Grid:{grid}" if grid else "",f"CQ{cqzone}" if cqzone else ""] if p]
            il=QLabel("  •  ".join(parts));il.setObjectName("info");il.setAlignment(Qt.AlignmentFlag.AlignCenter);il.setWordWrap(True);layout.addWidget(il)
        if self.bearing is not None:
            src=f" ({self.bearing_source})" if self.bearing_source not in ("","qrz") else ""
            bl=QLabel(f"Bearing: {self.bearing:.0f}°  •  {self.distance_mi:,.0f} mi{src}");bl.setObjectName("bearing");bl.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(bl)
            if PI_IP:
                rb=QPushButton(f"→ Point Rotor to {self.bearing:.0f}°");rb.setObjectName("rotor");rb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor));rb.clicked.connect(self._point_rotor);layout.addWidget(rb)
        self._div(layout)
        form=QFormLayout();form.setSpacing(8)
        self.rst_sent=QLineEdit("59");self.rst_rcvd=QLineEdit("59")
        self.notes=QLineEdit();self.notes.setPlaceholderText("Optional notes...")
        form.addRow(QLabel("RST Sent:"),self.rst_sent)
        form.addRow(QLabel("RST Rcvd:"),self.rst_rcvd)
        form.addRow(QLabel("Notes:"),self.notes)
        layout.addLayout(form)
        btn=QHBoxLayout();btn.setSpacing(10)
        cb=QPushButton("Cancel");cb.setObjectName("cancel");cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor));cb.clicked.connect(self.reject)
        lb=QPushButton("✓ Log QSO");lb.setObjectName("log");lb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor));lb.clicked.connect(self.accept);lb.setDefault(True)
        btn.addWidget(cb);btn.addWidget(lb);layout.addLayout(btn)
        self.setWindowTitle("Log QSO?")
        self._freq_timer=QTimer();self._freq_timer.timeout.connect(self._check_still_on_freq);self._freq_timer.start(500)
    def _div(self,layout):
        d=QFrame();d.setObjectName("divider");d.setFrameShape(QFrame.Shape.HLine);d.setFixedHeight(1);layout.addWidget(d)
    def get_values(self): return self.rst_sent.text() or "59",self.rst_rcvd.text() or "59",self.notes.text()
    def _check_still_on_freq(self):
        with freq_lock: cf_mhz=current_freq/1e6
        if abs(cf_mhz-self.freq_mhz)>=0.005:
            print(f"  VFO moved away ({cf_mhz:.3f} vs {self.freq_mhz:.3f}) — closing dialog")
            self.reject()
    def _point_rotor(self):
        threading.Thread(target=send_rotor_bearing,args=(self.bearing,),daemon=True).start()
class SpotSignal(QObject):
    spot_clicked=pyqtSignal(str,float,str,dict)
    multi_spot=pyqtSignal(list,float,str)
spot_signal=SpotSignal()
class SpotPickerDialog(QDialog):
    def __init__(self,spots,freq_mhz,parent=None):
        super().__init__(parent)
        self.selected=None
        self.freq_mhz=freq_mhz
        self.setWindowTitle("Multiple Spots Found")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Dialog)
        self.setMinimumWidth(350)
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        for child in self.findChildren(QPushButton):
            child.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet("""
            QDialog{background:#1e1e2e;color:#cdd6f4;font-family:'Courier New',monospace;cursor:default;}
            QLabel{color:#cdd6f4;font-size:13px;}
            QLabel#title{color:#fab387;font-size:15px;font-weight:bold;}
            QLabel#freq{color:#f5c2e7;font-size:12px;}
            QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;border-radius:6px;padding:8px 12px;font-size:13px;text-align:left;}
            QPushButton:hover{background:#45475a;border:1px solid #89b4fa;}
            QPushButton#cancel{background:#f38ba8;color:#1e1e2e;border:none;border-radius:6px;padding:8px 12px;font-weight:bold;}
        """)
        layout=QVBoxLayout(self); layout.setSpacing(8); layout.setContentsMargins(20,20,20,20)
        title=QLabel("📡 Multiple Spots Found"); title.setObjectName("title"); title.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(title)
        freq_label=QLabel(f"Near {freq_mhz:.3f} MHz — select a station:"); freq_label.setObjectName("freq"); freq_label.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(freq_label)
        d=QFrame(); d.setFrameShape(QFrame.Shape.HLine); d.setStyleSheet("background:#45475a;"); d.setFixedHeight(1); layout.addWidget(d)
        for sf,info in spots:
            cs=info['callsign']; md=info.get('mode','USB')
            btn=QPushButton(f"  {cs}   {sf:.3f} MHz  {md}")
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(lambda checked,s=(sf,info): self._pick(s))
            layout.addWidget(btn)
        d2=QFrame(); d2.setFrameShape(QFrame.Shape.HLine); d2.setStyleSheet("background:#45475a;"); d2.setFixedHeight(1); layout.addWidget(d2)
        cb=QPushButton("Cancel"); cb.setObjectName("cancel"); cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor)); cb.clicked.connect(self.reject); layout.addWidget(cb)
        self._freq_timer=QTimer(); self._freq_timer.timeout.connect(self._check_still_on_freq); self._freq_timer.start(500)

    def _check_still_on_freq(self):
        with freq_lock: cf_mhz=current_freq/1e6
        if abs(cf_mhz-self.freq_mhz)>=0.005:
            print(f"  VFO moved away ({cf_mhz:.3f} vs {self.freq_mhz:.3f}) — closing picker")
            self.reject()

    def _pick(self,spot):
        self.selected=spot
        self.accept()

def show_qso_dialog(callsign,freq_mhz,mode,qrz_data):
    global _dialog_open,_shown_freq
    if _dialog_open: print("  Dialog already open, skipping"); return
    _dialog_open=True
    with _spot_timer_lock:
        if _spot_timer is not None: _spot_timer.cancel()
    with _shown_freq_lock: _shown_freq=freq_mhz
    try:
        dialog=QSODialog(callsign,freq_mhz,mode,qrz_data)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            rst_sent,rst_rcvd,notes=dialog.get_values()
            log_qso(callsign,freq_mhz,mode,rst_sent,rst_rcvd,notes)
        else: print("  QSO cancelled")
    finally:
        _dialog_open=False
        with _shown_freq_lock: _shown_freq=None
def handle_rigctld_client(conn,addr):
    try:
        buf=""
        while True:
            data=conn.recv(256).decode(errors='ignore')
            if not data: break
            buf+=data
            while '\n' in buf:
                cmd,buf=buf.split('\n',1);cmd=cmd.strip()
                if not cmd: continue
                resp=b""
                if all(c in 'fmvt' for c in cmd) and len(cmd)>1:
                    for c in cmd:
                        if c=='f': resp+=f"{get_freq()}\nRPRT 0\n".encode()
                        elif c=='m': resp+=f"{get_mode()}\n3000\nRPRT 0\n".encode()
                        elif c=='v': resp+=b"VFOA\nRPRT 0\n"
                        elif c=='t': resp+=b"0\nRPRT 0\n"
                elif cmd in('f','\\get_freq'): resp=f"{get_freq()}\nRPRT 0\n".encode()
                elif cmd in('m','\\get_mode'): resp=f"{get_mode()}\n3000\nRPRT 0\n".encode()
                elif cmd in('v','\\get_vfo'): resp=b"VFOA\nRPRT 0\n"
                elif cmd in('t','\\get_ptt'): resp=b"0\nRPRT 0\n"
                elif cmd=='_': resp=b"FLEX-8400\nRPRT 0\n"
                else: resp=b"RPRT 0\n"
                conn.sendall(resp)
    except: pass
    finally: conn.close()
def start_rigctld_server():
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(("127.0.0.1",RIGCTLD_PORT));s.listen(5)
    print(f"rigctld emulator on port {RIGCTLD_PORT}")
    while True:
        try:
            conn,addr=s.accept()
            threading.Thread(target=handle_rigctld_client,args=(conn,addr),daemon=True).start()
        except: pass
def lookup_and_emit(callsign,freq_mhz,mode):
    qrz_data=qrz_lookup(callsign)
    spot_signal.spot_clicked.emit(callsign,freq_mhz,mode,qrz_data)

def pick_and_emit(spots,freq_mhz,mode):
    """Emit signal to show picker on main thread"""
    if len(spots)==1:
        callsign=spots[0][1]['callsign']
        threading.Thread(target=lookup_and_emit,args=(callsign,freq_mhz,mode),daemon=True).start()
        return
    spot_signal.multi_spot.emit(spots,freq_mhz,mode)

def show_spot_picker(spots,freq_mhz,mode):
    """Called on main thread - show picker dialog"""
    global _dialog_open,_shown_freq
    if _dialog_open: print("  Dialog already open, skipping"); return
    _dialog_open=True
    with _spot_timer_lock:
        if _spot_timer is not None: _spot_timer.cancel()
    with _shown_freq_lock: _shown_freq=freq_mhz
    try:
        picker=SpotPickerDialog(spots,freq_mhz)
        if picker.exec()==QDialog.DialogCode.Accepted and picker.selected:
            sf,info=picker.selected
            callsign=info['callsign']
            threading.Thread(target=lookup_and_emit,args=(callsign,sf,mode),daemon=True).start()
        else:
            print("  Spot selection cancelled")
    finally:
        _dialog_open=False
        with _shown_freq_lock: _shown_freq=None
def monitor_flex():
    global current_freq,current_mode,_shown_freq
    print("Connecting to Flex 8400...")
    while True:
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            s.connect((FLEX_HOST,FLEX_PORT));s.settimeout(3)
            print("Connected to Flex 8400!")
            time.sleep(1)
            try: s.recv(65536)
            except: pass
            s.send(b'C1|client program FlexMonitor\n');time.sleep(0.2)
            s.send(b'C2|sub slice all\n');time.sleep(0.2)
            s.send(b'C3|sub spot all\n');time.sleep(0.5)
            try: s.recv(65536)
            except: pass
            print("Monitoring frequency + spots...")
            buf=""
            while True:
                try:
                    chunk=s.recv(4096).decode(errors='ignore');buf+=chunk
                    while '\n' in buf:
                        line,buf=buf.split('\n',1);line=line.strip()
                        if '|spot ' in line and 'callsign=' in line and 'removed' not in line:
                            cs=re.search(r'callsign=(\S+)',line)
                            rf=re.search(r'rx_freq=(\d+\.\d+)',line)
                            mm=re.search(r'mode=(\w+)',line)
                            if cs and rf:
                                with spot_lock: spot_db[float(rf.group(1))]={'callsign':cs.group(1),'mode':mm.group(1) if mm else 'USB'}
                        if 'slice' in line and 'RF_frequency' in line:
                            fm=re.search(r'RF_frequency=(\d+\.\d+)',line)
                            mm=re.search(r'mode=(\w+)',line)
                            if fm:
                                new_freq_mhz=float(fm.group(1));new_freq_hz=round(new_freq_mhz*1e6/100)*100
                                new_mode=mm.group(1) if mm else "USB"
                                with freq_lock:
                                    changed=(new_freq_hz!=current_freq)
                                    current_freq=new_freq_hz;current_mode=new_mode
                                if changed:
                                    spots=find_spots(new_freq_mhz)
                                    mode=MODE_MAP.get(new_mode,new_mode)
                                    with _shown_freq_lock: sf=_shown_freq
                                    if sf is not None and abs(new_freq_mhz-sf)>=0.003:
                                        with _shown_freq_lock: _shown_freq=None; sf=None
                                    if spots:
                                        if sf is not None and abs(new_freq_mhz-sf)<0.003:
                                            pass  # already showed dialog here, wait for VFO to move away
                                        else:
                                            names=",".join([s[1]['callsign'] for s in spots])
                                            print(f"\n✓ {new_freq_mhz:.3f} MHz {new_mode} → {names}")
                                            schedule_spot_dialog(spots,new_freq_mhz,mode)
                                    else:
                                        with _spot_timer_lock:
                                            if _spot_timer is not None: _spot_timer.cancel()
                                        print(f"\n  {new_freq_mhz:.3f} MHz {new_mode} (no spot)")
                except socket.timeout: continue
                except Exception as e: print(f"Flex lost: {e}"); break
            s.close()
        except Exception as e: print(f"Flex failed: {e}")
        print("Reconnecting in 5s...");time.sleep(5)
if __name__=="__main__":
    print("=== Flex 8400 → Log4OM Bridge ===\n")
    subprocess.run(["pkill","rigctld"],capture_output=True);time.sleep(1)
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False)
    spot_signal.spot_clicked.connect(show_qso_dialog)
    spot_signal.multi_spot.connect(show_spot_picker)
    threading.Thread(target=qrz_get_session,daemon=True).start()
    threading.Thread(target=get_solar_data,daemon=True).start()
    threading.Thread(target=start_rigctld_server,daemon=True).start()
    threading.Thread(target=monitor_flex,daemon=True).start()
    print("Ready! Click a spot in AetherSDR to log a QSO.\n")

    # ─── System Tray Icon ─────────────────────────────────────────────────────
    def create_tray_icon():
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#a6e3a1"))
        painter = QPainter(pixmap)
        font = painter.font()
        font.setPointSize(22)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1e1e2e"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "73")
        painter.end()
        return QIcon(pixmap)

    def show_manual_qso():
        callsign, ok = QInputDialog.getText(None, "Manual QSO", "Enter callsign:")
        if ok and callsign.strip():
            callsign = callsign.strip().upper()
            with freq_lock:
                freq_mhz = current_freq / 1e6
                mode = MODE_MAP.get(current_mode, current_mode)
            threading.Thread(target=lookup_and_emit, args=(callsign, freq_mhz, mode), daemon=True).start()

    tray = QSystemTrayIcon(create_tray_icon(), app)
    tray_menu = QMenu()
    manual_action = tray_menu.addAction("📝 Log Manual QSO")
    manual_action.triggered.connect(show_manual_qso)
    tray_menu.addSeparator()
    status_action = tray_menu.addAction("📡 Flex to Log4OM Bridge - Running")
    status_action.setEnabled(False)
    tray_menu.addSeparator()
    quit_action = tray_menu.addAction("❌ Quit")
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(tray_menu)
    tray.setToolTip("Flex to Log4OM Bridge - Running")
    tray.activated.connect(lambda reason: show_manual_qso() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    print("System tray icon active - right-click for options")

    def recolor_worked_spots():
        """On startup, recolor spots worked in last 24 hours"""
        try:
            time.sleep(5)  # Wait for spots to load first
            conn=sqlite3.connect(DB_LOCAL)
            cursor=conn.cursor()
            cursor.execute("""SELECT DISTINCT callsign FROM Log 
                WHERE qsodate >= datetime('now', '-24 hours')""")
            worked=[row[0] for row in cursor.fetchall()]
            conn.close()
            if worked:
                print(f"Recoloring {len(worked)} worked stations from last 24h...")
                for callsign in worked:
                    change_spot_color(callsign)
                    time.sleep(0.5)
        except Exception as e:
            print(f"Recolor error: {e}")

    threading.Thread(target=recolor_worked_spots,daemon=True).start()
    sys.exit(app.exec())
