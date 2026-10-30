import sys
import json
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, 
    QVBoxLayout, QHBoxLayout, QWidget, 
    QLineEdit, QLabel, QGroupBox, QTextEdit
)
from PyQt6.QtCore import QThread, pyqtSignal
from paho.mqtt import client as mqtt_client

class MQTTWorker(QThread):
    gui_log_signal = pyqtSignal(str)
    rpi_log_signal = pyqtSignal(str, str)
    telemetry_signal = pyqtSignal(bool, float)
    connection_signal = pyqtSignal(bool)
    # Arayüzdeki logları ve telemetri verilerini GUI'ye iletmek için sinyaller tanımlandı.

    def __init__(self, broker="127.0.0.1", port=1883, 
                 topic_cmd="cezeri/drone/command", topic_status="cezeri/drone/status"):
        super().__init__()
        self.broker = broker
        self.port = port
        self.topic_cmd = topic_cmd
        self.topic_status = topic_status
        self.is_connected = False
        # Raspberry Pi ve drone ile MQTT üzerinden iletişim kurmak için gerekli parametreler ve bağlantı durumu değişkeni tanımlandı.

        self.client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
        )
        # MQTT istemcisi(client) oluşturuldu.

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        # İstemciye bağlantı, mesaj alma ve bağlantı kesilme olayları için geri çağırma fonksiyonları atandı.

    def run(self):
        try:
            self.gui_log_signal.emit(f"[MQTT] Connecting to {self.broker}:{self.port}...")
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_forever()
        except Exception as e:
            self.gui_log_signal.emit(f"[MQTT ERROR] Connection failed: {str(e)}")
            self.connection_signal.emit(False)
        # run metodu, MQTT istemcisini başlatır ve bağlantıyı yönetir. Bağlantı sırasında oluşabilecek hatalar GUI'ye iletilir.

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self.is_connected = True
            self.gui_log_signal.emit("[MQTT] Connected to Broker successfully!")
            self.connection_signal.emit(True)
            self.client.subscribe(self.topic_status)
        else:
            self.is_connected = False
            self.connection_signal.emit(False)
        # on_connect metodu, MQTT brokerına başarılı bir şekilde bağlanıldığında veya başarısız olduğunda ilgili sinyalleri tetikler ve durum mesajlarını GUI'ye iletir.

    def on_message(self, client, userdata, msg):
        try:
            payload_str = msg.payload.decode('utf-8')
            status_data = json.loads(payload_str)
            
            event = status_data.get("event", "INFO")
            message = status_data.get("message", "")
            is_armed = status_data.get("is_armed", False)
            altitude = status_data.get("altitude", 0.0)

            self.rpi_log_signal.emit(event, message)
            self.telemetry_signal.emit(is_armed, altitude)
        except Exception as e:
            self.gui_log_signal.emit(f"[PARSE ERROR] Failed to parse status message: {str(e)}")
        # on_message metodu, MQTT brokerından gelen mesajları işler. Mesajın içeriği JSON formatında çözülür ve ilgili telemetri verileri ile durum mesajları GUI'ye iletilir.

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self.is_connected = False
        self.connection_signal.emit(False)
        # on_disconnect metodu, MQTT brokerından bağlantı kesildiğinde tetiklenir ve GUI'ye bağlantı durumunu iletir.

    def publish_command(self, command_dict):
        if self.is_connected:
            payload = json.dumps(command_dict)
            self.client.publish(self.topic_cmd, payload)
            self.gui_log_signal.emit(f"[SENT] Topic: {self.topic_cmd} | Payload: {payload}")
        else:
            self.gui_log_signal.emit("[MQTT ERROR] Cannot send command: Not connected!")
        # publish_command metodu, GUI'den gelen komutları MQTT brokerına gönderir. Bağlantı durumu kontrol edilir ve gönderilen komutlar GUI log paneline yazılır.

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
        self.quit()
        self.wait()
        # stop metodu, MQTT istemcisini durdurur ve bağlantıyı keser. Bu, uygulama kapatıldığında veya MQTT iletişimi sonlandırıldığında çağrılır.


class DroneControlGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.mqtt_worker = MQTTWorker()
        self.mqtt_worker.gui_log_signal.connect(self.append_gui_log)
        self.mqtt_worker.rpi_log_signal.connect(self.append_rpi_log)
        self.mqtt_worker.telemetry_signal.connect(self.update_telemetry)
        self.mqtt_worker.connection_signal.connect(self.update_connection_status)
        self.mqtt_worker.start()
        # DroneControlGUI sınıfı, ana pencereyi oluşturur ve kullanıcı arayüzünü başlatır. MQTTWorker ile bağlantı kurar ve sinyalleri GUI bileşenlerine bağlar.

    def init_ui(self):
        self.setWindowTitle("Cezeri - Drone Control Interface")
        self.setGeometry(100, 100, 600, 600)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()

        status_layout = QHBoxLayout()
        self.lbl_status = QLabel("MQTT: Connecting...", self)
        self.lbl_status.setStyleSheet("font-weight: bold; color: orange;")
        
        self.lbl_arm_state = QLabel("ARM State: UNKNOWN", self)
        self.lbl_arm_state.setStyleSheet("font-weight: bold; color: gray; font-size: 13px;")

        self.lbl_altitude_live = QLabel("Altitude: 0.0m", self)
        self.lbl_altitude_live.setStyleSheet("font-weight: bold; color: blue; font-size: 13px;")

        status_layout.addWidget(self.lbl_status)
        status_layout.addStretch()
        status_layout.addWidget(self.lbl_arm_state)
        status_layout.addSpacing(15)
        status_layout.addWidget(self.lbl_altitude_live)
        main_layout.addLayout(status_layout)

        self.btn_arm = QPushButton("ARM", self)
        self.btn_disarm = QPushButton("DISARM", self)
        self.btn_land = QPushButton("LAND", self)

        self.btn_arm.clicked.connect(lambda: self.send_command("ARM"))
        self.btn_disarm.clicked.connect(lambda: self.send_command("DISARM"))
        self.btn_land.clicked.connect(lambda: self.send_command("LAND"))

        main_layout.addWidget(self.btn_arm)
        main_layout.addWidget(self.btn_disarm)

        takeoff_group = QGroupBox("Takeoff Settings")
        takeoff_layout = QHBoxLayout()
        self.lbl_altitude = QLabel("Target Alt (m):")
        self.input_altitude = QLineEdit("10")
        self.btn_takeoff = QPushButton("TAKEOFF")
        self.btn_takeoff.clicked.connect(self.handle_takeoff)

        takeoff_layout.addWidget(self.lbl_altitude)
        takeoff_layout.addWidget(self.input_altitude)
        takeoff_layout.addWidget(self.btn_takeoff)
        takeoff_group.setLayout(takeoff_layout)
        main_layout.addWidget(takeoff_group)

        move_group = QGroupBox("3D Movement Settings (Relative)")
        move_layout = QHBoxLayout()
        self.input_pos_x = QLineEdit("0")
        self.input_pos_y = QLineEdit("0")
        self.input_pos_z = QLineEdit("0")
        self.btn_move = QPushButton("MOVE 3D")
        self.btn_move.clicked.connect(self.handle_move)

        move_layout.addWidget(QLabel("X (North):"))
        move_layout.addWidget(self.input_pos_x)
        move_layout.addWidget(QLabel("Y (East):"))
        move_layout.addWidget(self.input_pos_y)
        move_layout.addWidget(QLabel("Z (Up):"))
        move_layout.addWidget(self.input_pos_z)
        move_layout.addWidget(self.btn_move)
        move_group.setLayout(move_layout)
        main_layout.addWidget(move_group)

        main_layout.addWidget(self.btn_land)

        logs_layout = QHBoxLayout()

        gui_log_group = QGroupBox("GUI Outgoing Logs")
        gui_log_box = QVBoxLayout()
        self.txt_gui_logs = QTextEdit()
        self.txt_gui_logs.setReadOnly(True)
        gui_log_box.addWidget(self.txt_gui_logs)
        gui_log_group.setLayout(gui_log_box)

        rpi_log_group = QGroupBox("RPi & Vehicle Status Feedback")
        rpi_log_box = QVBoxLayout()
        self.txt_rpi_logs = QTextEdit()
        self.txt_rpi_logs.setReadOnly(True)
        rpi_log_box.addWidget(self.txt_rpi_logs)
        rpi_log_group.setLayout(rpi_log_box)

        logs_layout.addWidget(gui_log_group)
        logs_layout.addWidget(rpi_log_group)
        main_layout.addLayout(logs_layout)

        central_widget.setLayout(main_layout)
        # GUI bileşenleri oluşturuldu ve düzenlendi.

    def send_command(self, command_type, params=None):
        cmd_data = {"command": command_type}
        if params:
            cmd_data.update(params)
        self.mqtt_worker.publish_command(cmd_data)
        # send_command metodu, GUI'den gelen komutları MQTTWorker aracılığıyla MQTT brokerına gönderir. Komut türü ve parametreler JSON formatında hazırlanır.

    def handle_takeoff(self):
        try:
            alt = float(self.input_altitude.text())
            self.send_command("TAKEOFF", {"altitude": alt})
        except ValueError:
            self.append_gui_log("[GUI ERROR] Altitude must be a valid number!")
        # handle_takeoff metodu, kullanıcıdan alınan hedef irtifa değerini alır ve TAKEOFF komutunu MQTT brokerına gönderir.

    def handle_move(self):
        try:
            x = float(self.input_pos_x.text())
            y = float(self.input_pos_y.text())
            z = float(self.input_pos_z.text())
            self.send_command("MOVE", {"x": x, "y": y, "z": z})
        except ValueError:
            self.append_gui_log("[GUI ERROR] X, Y, Z must be valid numbers!")
        # handle_move metodu, kullanıcıdan alınan 3D hareket koordinatlarını alır ve MOVE komutunu MQTT brokerına gönderir.

    def append_gui_log(self, text):
        self.txt_gui_logs.append(text)
        # append_gui_log metodu, GUI log paneline yeni mesajlar ekler.

    def append_rpi_log(self, event, message):
        if event == "SILENT_TELEMETRY" or not message:
            return
        color = "black"
        if event == "SUCCESS":
            color = "green"
        elif event == "WARNING":
            color = "orange"
        elif event == "ERROR":
            color = "red"
        elif event == "INFO":
            color = "blue"

        formatted_msg = f"<span style='color:{color}; font-weight:bold;'>[{event}]</span> {message}"
        self.txt_rpi_logs.append(formatted_msg)
        # append_rpi_log metodu, Raspberry Pi ve drone'dan gelen durum mesajlarını RPI log paneline ekler.

    def update_telemetry(self, is_armed, altitude):
        if is_armed:
            self.lbl_arm_state.setText("ARM State: ARMED")
            self.lbl_arm_state.setStyleSheet("font-weight: bold; color: green; font-size: 13px;")
        else:
            self.lbl_arm_state.setText("ARM State: DISARMED")
            self.lbl_arm_state.setStyleSheet("font-weight: bold; color: red; font-size: 13px;")

        self.lbl_altitude_live.setText(f"Altitude: {altitude:.1f}m")
        # update_telemetry metodu, drone'un anlık telemetri bilgilerini günceller.

    def update_connection_status(self, connected):
        if connected:
            self.lbl_status.setText("MQTT: Connected")
            self.lbl_status.setStyleSheet("font-weight: bold; color: green;")
        else:
            self.lbl_status.setText("MQTT: Disconnected")
            self.lbl_status.setStyleSheet("font-weight: bold; color: red;")
        # update_connection_status metodu, MQTT bağlantı durumunu GUI üzerinde günceller.

    def closeEvent(self, event):
        self.mqtt_worker.stop()
        event.accept()
        # closeEvent metodu, uygulama kapatıldığında MQTTWorker'ı durdurur ve bağlantıyı keser.


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DroneControlGUI()
    window.show()
    sys.exit(app.exec())
    # Uygulama başlatıldığında, DroneControlGUI sınıfı oluşturulur ve ana pencere gösterilir.