# 🚁 Otonom Drone Kontrol Arayüzü (GUI) ve Ara Katman Yazılımı

Bu proje; masaüstü grafik kullanıcı arayüzü (GUI) üzerinden otonom bir drone'a komut göndermeyi sağlayan 3 katmanlı, çok kanallı (multi-threaded) haberleşme mimarisine sahip bir kontrol sistemidir.

Arayüz (PyQt6), uçuş komutlarını **MQTT** protokolü üzerinden araç içi bilgisayara (**Raspberry Pi - RPi**) iletir. RPi üzerindeki dinleyici yazılım ise **pymavlink** kütüphanesini kullanarak gelen komutları **ArduPilot** uçuş kontrolörüne aktarır.

## 📐 Sistem Mimarisi & Veri Akışı

```
[ Kullanıcı (GUI) ]
         │
    (PyQt6 GUI)
         │
         ▼
[ MQTT Publisher (QThread) ] ──(MQTT: 1883)──► [ Mosquitto Broker ]
                                                     │
                                               (MQTT: 1883)
                                                     │
                                                     ▼
[ ArduPilot Otopilot ] ◄──────(pymavlink)───── [ RPi Middleware (Subscriber) ]
```

### Veri Akış Aşamaları:

1. **Arayüz Katmanı (PyQt6):** Kullanıcı GUI üzerindeki bir butona bastığında komut doğrulaması yapılır ve arayüzün donmaması için ayrı bir `QThread` içerisinde MQTT mesajı olarak yayınlanır (`publish`).

2. **Mesajlaşma Katmanı (MQTT Broker):** Yerel Mosquitto broker, GUI ile RPi arasındaki mesaj iletimini yönetir.

3. **Ara Katman (Raspberry Pi Middleware):** RPi üzerindeki Python kodu ilgili kanala abonedir (`subscribe`). Gelen mesajı çözümler, güvenlik filtrelerinden geçirir ve `pymavlink` aracılığıyla ArduPilot uçuş kontrolörüne MAVLink paketi olarak aktarır.

4. **Otopilot Katmanı (ArduPilot):** MAVLink komutlarını karşılayan ve motor/hava aracı yönlendirmelerini icra eden uçuş kontrol sistemidir.

## 🛠️ Teknik Özellikler ve Kurulum

* **Grafik Arayüz (GUI):** PyQt6
* **Ara Katman İletişimi:** `pymavlink` (ArduPilot MAVLink haberleşmesi)
* **Mesajlaşma Protokolü:** `paho-mqtt` (MQTT Broker entegrasyonu)
* **Yazılım Mimarisi:** Nesne Yönelimli Programlama (OOP) ve Çok Kanallı Çalışma (Multi-threading / `QThread`)

### Bağımlılıkların Yüklenmesi

```bash
pip install -r requirements.txt
```

---

## 🚀 Sistem Bileşenlerinin Çalıştırılması

Sistemin tam senkronize çalışması için bileşenleri sırasıyla başlatın:

### 1. MQTT Broker'ın (Mosquitto) İndirilmesi ve Aktif Edilmesi

Projenin çalışması için bilgisayarınızda veya Raspberry Pi üzerinde bir MQTT broker'ın çalışıyor olması gerekir.

#### Mosquitto Kurulumu (Broker Yoksa):
* **Windows:** [Mosquitto İndirme Sayfası](https://mosquitto.org/download/)'ndan `.exe` yükleyicisini indirip kurun veya terminalden yükleyin:
  ```powershell
  winget install EclipseFoundation.Mosquitto
  ```
* **Linux / Raspberry Pi OS:**
  ```bash
  sudo apt update
  sudo apt install mosquitto mosquitto-clients -y
  ```

#### Mosquitto Yapılandırması ve Başlatılması:
Mosquitto'nun dış bağlantıları kabul etmesi için `mosquitto.conf` dosyasının en altına şu satırları ekleyin:
```text
listener 1883
allow_anonymous true
```

Servisi başlatın:
* **Windows (CMD/PowerShell Admin):**
  ```powershell
  net start mosquitto
  ```
* **Linux / RPi OS:**
  ```bash
  sudo systemctl start mosquitto
  ```

---

### 2. Raspberry Pi Ara Katman Yazılımının Başlatılması (Terminal 1)

```bash
python "Raspberry Pi.py"
```

*Script, ArduPilot otopilotu ile MAVLink bağlantısını kuracak (`tcp:127.0.0.1:5760`), kalp atışını (Heartbeat) doğrulayacak ve Mosquitto MQTT kanalını dinlemeye başlayacaktır.*

---

### 3. Yer Kontrol İstasyonu Arayüzünün Başlatılması (Terminal 2)

```bash
python "Drone Control GUI.py"
```

*PyQt6 arayüzü açılacak, ayrı bir `QThread` üzerinde yerel MQTT broker'a bağlanacak ve butonlar aracılığıyla komut göndermeye hazır hale gelecektir.*

---

## 🎮 Temel Uçuş Komutları ve Güvenlik Kuralları

Arayüz üzerinden 5 temel uçuş komutu gönderilebilmektedir:

* **ARM:** Motorları kullanıma hazır duruma getirir.
* **TAKEOFF:** Belirtilen hedef yüksekliğe otonom kalkış gerçekleştirir.
* **MOVE:** Drone'u istenen yön ve 3D konum koordinatlarına hareket ettirir (`LOCAL_POSITION_NED` doğrulaması ile).
* **LAND:** Drone'u bulunduğu konuma güvenli şekilde indirir.
* **DISARM:** Motorları pasif duruma getirir *(Araç havadayken serbest düşüşü engellemek için DISARM komutları reddedilir)*.

## 📁 Proje Dosya Yapısı

```text
.
├── Drone Control GUI.py   # PyQt6 tabanlı Yer Kontrol İstasyonu (Multi-threaded GUI & MQTT)
├── Raspberry Pi.py        # RPi tarafı MQTT Subscriber ve pymavlink ArduPilot entegrasyonu
├── requirements.txt       # Kullanılan Python kütüphaneleri
├── .gitignore             # Git takibine girmeyen geçici/gereksiz dosyalar
└── README.md              # Proje dokümantasyon dosyası
```