import time
import json
import threading
from paho.mqtt import client as mqtt_client
from pymavlink import mavutil

class DroneController:
    def __init__(self, connection_string="tcp:127.0.0.1:5760", broker="127.0.0.1", port=1883, 
                 topic_cmd="cezeri/drone/command", topic_status="cezeri/drone/status"):
        self.connection_string = connection_string
        self.broker = broker
        self.port = port
        self.topic_cmd = topic_cmd
        self.topic_status = topic_status
        
        #MAVLink ve MQTT istemci nesneleri
        self.vehicle = None
        self.mqtt_client = None
        self.is_vehicle_armed = False
        self.current_relative_alt = 0.0
        self.running = True

        #Görev takip durum bayrakları
        self.target_takeoff_alt = None
        self.is_landing_tracked = False

        #Lokal NED koordinatları ve 3D hareket takip parametreleri
        self.current_local_x = 0.0
        self.current_local_y = 0.0
        self.current_local_z = 0.0
        self.target_pos_x = None
        self.target_pos_y = None
        self.target_pos_z = None
        self.is_moving_tracked = False

    def connect_vehicle(self):
        #Otopilot ile TCP/UDP üzerinden MAVLink bağlantısını kurar ve ilk Heartbeat paketini bekler.
        print(f"[MAVLINK] Connecting to vehicle on {self.connection_string}...")
        self.vehicle = mavutil.mavlink_connection(self.connection_string)
        self.vehicle.wait_heartbeat()
        print(f"[MAVLINK] Heartbeat received! (System {self.vehicle.target_system}, Component {self.vehicle.target_component})")
        self.request_telemetry_stream()

    def request_telemetry_stream(self):
        #Otopilottan anlık konum ve durum telemetri veri akışlarını (10 Hz) talep eder.
        self.vehicle.mav.request_data_stream_send(
            self.vehicle.target_system,
            self.vehicle.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_POSITION,
            10, 1
        )
        self.vehicle.mav.request_data_stream_send(
            self.vehicle.target_system,
            self.vehicle.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
            10, 1
        )

    @property
    def is_in_air(self):
        #Aracın hem ARM durumda olduğunu hem de güvenli bağıl yükseklikte (>0.5m) bulunduğunu doğrular.
        return self.is_vehicle_armed and (self.current_relative_alt > 0.5)

    def telemetry_loop(self):
        #MAVLink paketlerini işleyen, durum değişikliklerini yakalayan ve görev takiplerini yöneten ana döngü.
        last_telemetry_pub = 0

        while self.running:
            try:
                msg = self.vehicle.recv_match(blocking=True, timeout=0.1)
                if not msg or msg.get_srcSystem() != self.vehicle.target_system:
                    continue

                msg_type = msg.get_type()

                #Motor durum güncellemeleri ve oto-disarm durumlarının takibi
                if msg_type == 'HEARTBEAT':
                    src_comp = msg.get_srcComponent()
                    if src_comp == self.vehicle.target_component or src_comp == mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1:
                        real_armed_state = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                        
                        if real_armed_state != self.is_vehicle_armed:
                            self.is_vehicle_armed = real_armed_state
                            status_str = "ARMED" if real_armed_state else "DISARMED"
                            self.publish_status("INFO", f"Flight Controller State: {status_str}", is_armed=real_armed_state)

                            #Araç yere indiğinde motorlar kapanırsa aktif görevleri sıfırla
                            if not real_armed_state and self.is_moving_tracked:
                                self.is_moving_tracked = False
                                self.target_pos_x = None
                                self.publish_status("SUCCESS", "3D MOVE completed! Ground reached and vehicle disarmed.")

                            if not real_armed_state and self.is_landing_tracked:
                                self.is_landing_tracked = False
                                self.publish_status("SUCCESS", "LAND sequence completed successfully! Vehicle is grounded and disarmed.")
                
                #Lokal NED koordinat güncellemesi ve 3D hedef mesafe hesabı
                elif msg_type == 'LOCAL_POSITION_NED':
                    self.current_local_x = msg.x
                    self.current_local_y = msg.y
                    self.current_local_z = msg.z

                    if self.is_moving_tracked and self.target_pos_x is not None:
                        dx = self.target_pos_x - self.current_local_x
                        dy = self.target_pos_y - self.current_local_y
                        dz = self.target_pos_z - self.current_local_z
                        distance_to_target = (dx**2 + dy**2 + dz**2)**0.5

                        #Hedef noktaya varış kontrolü (<30cm tolerans)
                        if distance_to_target < 0.3:
                            self.is_moving_tracked = False
                            self.target_pos_x = None
                            self.publish_status("SUCCESS", "3D MOVE completed! Vehicle reached target position.")

                        #Erken Zemin Algılama: Aşağı inilirken zemin seviyesine (<=0.5m) yaklaşıldığında sıkışmayı önlemek için LAND moduna geç
                        elif self.current_relative_alt <= 0.5 and dz < 0:
                            self.is_moving_tracked = False
                            self.target_pos_x = None
                            self.set_mode("LAND")
                            self.publish_status("SUCCESS", "3D MOVE completed! Ground level reached, switching to LAND mode.")

                #Bağıl irtifa takibi ve periyodik sessiz telemetri yayını (5 Hz)
                elif msg_type == 'GLOBAL_POSITION_INT':
                    self.current_relative_alt = round(msg.relative_alt / 1000.0, 2)
                    
                    if self.target_takeoff_alt is not None:
                        if self.current_relative_alt >= (self.target_takeoff_alt * 0.95):
                            self.target_takeoff_alt = None
                            self.publish_status("SUCCESS", f"TAKEOFF completed! Target altitude reached.")

                    now = time.time()
                    if (now - last_telemetry_pub) > 0.2:
                        self.publish_silent_telemetry()
                        last_telemetry_pub = now

                #Gönderilen MAVLink komutlarının otopilot onay/red durumları
                elif msg_type == 'COMMAND_ACK':
                    if msg.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
                        if msg.result == 0:
                            self.publish_status("SUCCESS", "ARM/DISARM command ACCEPTED.")
                        else:
                            self.publish_status("ERROR", f"ARM/DISARM REJECTED (Code: {msg.result}). Check Pre-Arm/GPS status!")
                    elif msg.command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
                        if msg.result == 0:
                            self.publish_status("SUCCESS", "TAKEOFF command ACCEPTED. Cliping up...")
                        else:
                            self.publish_status("ERROR", "TAKEOFF REJECTED by Flight Controller.")

            except Exception as e:
                print(f"[TELEMETRY ERROR] {str(e)}")

    def publish_silent_telemetry(self):
        #GUI göstergelerini güncellemek için log oluşturmayan periyodik telemetri verisi gönderir.
        if self.mqtt_client and self.mqtt_client.is_connected():
            payload = json.dumps({
                "event": "SILENT_TELEMETRY",
                "message": "",
                "is_armed": self.is_vehicle_armed,
                "altitude": self.current_relative_alt
            })
            self.mqtt_client.publish(self.topic_status, payload)

    def publish_status(self, event_type, message, is_armed=None):
        #Olay ve durum bildirimlerini MQTT üzerinden GUI log paneline iletir.
        if self.mqtt_client and self.mqtt_client.is_connected():
            if is_armed is None:
                is_armed = self.is_vehicle_armed
            
            payload = json.dumps({
                "event": event_type,
                "message": message,
                "is_armed": is_armed,
                "altitude": self.current_relative_alt
            })
            self.mqtt_client.publish(self.topic_status, payload)

    def arm(self):
        #Aracı STABILIZE moduna alıp motorları devreye sokar.
        if self.is_vehicle_armed:
            self.publish_status("WARNING", "Vehicle is ALREADY ARMED in Flight Controller.", is_armed=True)
            return

        self.set_mode("STABILIZE")
        time.sleep(0.3)

        self.publish_status("INFO", "Sending ARM command...", is_armed=False)
        self.vehicle.mav.command_long_send(
            self.vehicle.target_system, self.vehicle.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0
        )

    def disarm(self):
        #Motorları kapatır. Havada DISARM yapılmasını engeller; zemindeyse (<=0.5m) ArduPilot'ın GUIDED mod kilitlenmesini aşmak için önce STABILIZE moduna geçer.
        if not self.is_vehicle_armed:
            self.publish_status("WARNING", "Vehicle is ALREADY DISARMED.", is_armed=False)
            return

        if self.current_relative_alt <= 0.5:
            self.set_mode("STABILIZE")
            time.sleep(0.2)

            self.publish_status("INFO", "Sending DISARM command...", is_armed=True)
            self.vehicle.mav.command_long_send(
                self.vehicle.target_system, self.vehicle.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 0, 0, 0, 0, 0, 0
            )
        else:
            self.publish_status("ERROR", f"Cannot DISARM while IN THE AIR! (Alt: {self.current_relative_alt:.1f}m). Land first!", is_armed=True)

    def takeoff(self, altitude):
        #GUIDED moda geçerek belirtilen hedef irtifaya otonom kalkış başlatır.
        if not self.is_vehicle_armed:
            self.publish_status("WARNING", "Cannot TAKEOFF: Vehicle must be ARMED first!", is_armed=False)
            return

        if self.is_in_air:
            self.publish_status("WARNING", f"Already IN THE AIR (Alt: {self.current_relative_alt:.1f}m). TAKEOFF ignored.", is_armed=True)
            return

        self.publish_status("INFO", f"Setting mode to GUIDED and initiating TAKEOFF to {altitude}m...", is_armed=True)
        self.set_mode("GUIDED")
        time.sleep(0.5)

        self.target_takeoff_alt = float(altitude)

        self.vehicle.mav.command_long_send(
            self.vehicle.target_system, self.vehicle.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, altitude
        )

    def move(self, x, y, z=0.0):
        #Mevcut konuma göre bağıl 3D hareket emri verir (MAV_FRAME_LOCAL_OFFSET_NED). NED kuralı gereği yukarı hareket için z değeri negatif (-z) gönderilir.
        if not self.is_vehicle_armed:
            self.publish_status("WARNING", "Cannot MOVE: Vehicle is DISARMED!", is_armed=False)
            return

        if not self.is_in_air:
            self.publish_status("WARNING", "Cannot MOVE: Vehicle is ON THE GROUND! Take off first.", is_armed=True)
            return

        self.target_pos_x = self.current_local_x + x
        self.target_pos_y = self.current_local_y + y
        self.target_pos_z = self.current_local_z + (-z)
        self.is_moving_tracked = True

        self.publish_status("INFO", f"Moving 3D Relative -> North(X): {x}m, East(Y): {y}m, Up(Z): {z}m...", is_armed=True)

        self.vehicle.mav.set_position_target_local_ned_send(
            0, self.vehicle.target_system, self.vehicle.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_OFFSET_NED, 
            0b0000111111111000, 
            x, y, -z, 0, 0, 0, 0, 0, 0, 0, 0    
        )

    def land(self):
        #Otopilota LAND komutu göndererek otonom iniş dizisini başlatır.
        if not self.is_in_air:
            self.publish_status("WARNING", "Vehicle is ALREADY ON THE GROUND. LAND command ignored.", is_armed=self.is_vehicle_armed)
            return

        self.publish_status("INFO", f"Initiating LAND sequence from {self.current_relative_alt:.1f}m...", is_armed=True)
        self.is_landing_tracked = True
        self.set_mode("LAND")

    def set_mode(self, mode_name):
        #Uçuş modunu değiştirmek için MAVLink kütüphanesindeki mod haritalamasını kullanır.
        mode_id = self.vehicle.mode_mapping().get(mode_name)
        if mode_id is None:
            self.publish_status("ERROR", f"Unknown mode: {mode_name}")
            return False
        
        self.vehicle.mav.set_mode_send(
            self.vehicle.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id
        )
        return True

    def setup_mqtt(self):
        #MQTT istemcisini oluşturur ve callback fonksiyonlarını bağlar.
        self.mqtt_client = mqtt_client.Client(
            callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
        )
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message

        print(f"[MQTT] Connecting to {self.broker}:{self.port}...")
        self.mqtt_client.connect(self.broker, self.port, keepalive=60)

    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties=None):
        #Broker bağlantısı sağlandığında komut başlığına (topic_cmd) abone olur.
        if reason_code == 0:
            print(f"[MQTT] Connected successfully! Subscribing to: {self.topic_cmd}")
            self.mqtt_client.subscribe(self.topic_cmd)
        else:
            print(f"[MQTT ERROR] Connection failed with code: {reason_code}")

    def on_mqtt_message(self, client, userdata, msg):
        #GUI'den gelen JSON komutlarını çözümler ve ilgili kontrol fonksiyonuna yönlendirir.
        try:
            payload_str = msg.payload.decode('utf-8')
            cmd_data = json.loads(payload_str)
            command = cmd_data.get("command")

            if command == "ARM":
                self.arm()
            elif command == "DISARM":
                self.disarm()
            elif command == "TAKEOFF":
                altitude = cmd_data.get("altitude", 10.0)
                self.takeoff(altitude)
            elif command == "MOVE":
                x = float(cmd_data.get("x", 0.0))
                y = float(cmd_data.get("y", 0.0))
                z = float(cmd_data.get("z", 0.0))
                self.move(x, y, z)
            elif command == "LAND":
                self.land()

        except Exception as e:
            print(f"[ERROR] Failed to process message: {str(e)}")

    def start(self):
        #MAVLink, MQTT ve arka plan telemetri iş parçacığını (thread) başlatarak dinlemeye geçer.
        self.connect_vehicle()
        self.setup_mqtt()
        
        t = threading.Thread(target=self.telemetry_loop, daemon=True)
        t.start()

        print("[SYSTEM] RPi Drone Controller active and listening for commands...")
        self.mqtt_client.loop_forever()

if __name__ == "__main__":
    controller = DroneController(connection_string="tcp:127.0.0.1:5760")
    controller.start()