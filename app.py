from flask import Flask, render_template, jsonify, request
import joblib
import pandas as pd
import paho.mqtt.client as mqtt
import threading
import requests
import time

app = Flask(__name__)

# ---------------- MQTT SETUP ----------------
BROKER = "35.154.62.193"
PORT = 1883
TOPIC_SOIL = "farmbot/soil"
TOPIC_COMMAND = "farmbot/command"
TOPIC_MOTOR = "farmbot/motor"

soil_value = 0  # default

def on_connect(client, userdata, flags, rc):
    print("✅ Connected to MQTT Broker with code", rc)
    client.subscribe(TOPIC_SOIL)

def on_message(client, userdata, msg):
    global soil_value
    if msg.topic == TOPIC_SOIL:
        try:
            soil_value = float(msg.payload.decode())
        except:
            soil_value = 0
        print("🌱 Soil Moisture:", soil_value)

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, PORT, 60)

def mqtt_loop():
    client.loop_forever()

threading.Thread(target=mqtt_loop, daemon=True).start()

# ---------------- WEATHER API ----------------
OWM_API_KEY = "7a25c4aa820baf7771ef29e0d33207f7"
LAT = "16.792877"
LON = "80.823128"

def get_weather_data():
    url = f"http://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={OWM_API_KEY}&units=metric"
    try:
        resp = requests.get(url)
        data = resp.json()
        temp = data["main"]["temp"]
        humidity = data["main"]["humidity"]
        rainfall = data.get("rain", {}).get("1h", 0)
        return {"temperature": temp, "humidity": humidity, "rainfall": rainfall}
    except Exception as e:
        print("⚠️ Weather API error:", e)
        return {"temperature": 0, "humidity": 0, "rainfall": 0}

# ---------------- ML MODEL ----------------
model = joblib.load('motor_status_model.pkl')
label_encoders = joblib.load('motor_status_label_encoders.pkl')

all_features = ['soil_moisture','humidity','temperature','Nitrogen','Phosphorus','Potassium',
                'rainfall_mm','crop_type','crop_stage','soil_type']
numeric_features = all_features[:7]
categorical_features = all_features[7:]

# ---------------- Helper functions ----------------
def get_dummy_inputs():
    return {
        "Nitrogen": 50,
        "Phosphorus": 50,
        "Potassium": 50,
        "crop_type": label_encoders['crop_type'].classes_[0],
        "crop_stage": label_encoders['crop_stage'].classes_[0],
        "soil_type": label_encoders['soil_type'].classes_[0]
    }

def predict_motor(soil, humidity, temp, rainfall):
    inputs = get_dummy_inputs()
    data = {
        "soil_moisture": soil,
        "humidity": humidity,
        "temperature": temp,
        "rainfall_mm": rainfall,
        **inputs
    }
    for feature in categorical_features:
        le = label_encoders[feature]
        data[feature] = le.transform([data[feature]])[0]

    df_input = pd.DataFrame([data], columns=all_features)
    pred = model.predict(df_input)[0]
    motor_status = label_encoders['motor_status'].inverse_transform([pred])[0]

    # Send motor status to MQTT
    client.publish(TOPIC_MOTOR, motor_status)
    print(f"📡 Motor status sent to MQTT: {motor_status}")

    return motor_status

# ---------------- FLASK ROUTES ----------------
dropdown_options = {col: label_encoders[col].classes_ for col in categorical_features}

@app.route('/', methods=['GET', 'POST'])
def index():
    prediction = None
    if request.method == 'POST':
        data = {feature: request.form[feature] for feature in all_features}
        for feature in numeric_features:
            data[feature] = float(data[feature])
        for feature in categorical_features:
            le = label_encoders[feature]
            data[feature] = le.transform([data[feature]])[0]
        df_input = pd.DataFrame([data], columns=all_features)
        pred = model.predict(df_input)[0]
        prediction = label_encoders['motor_status'].inverse_transform([pred])[0]
    return render_template('index.html', prediction=prediction, options=dropdown_options)

@app.route('/crops')
def crops():
    return render_template('crops.html')

@app.route('/analytics')
def analytics():
    return render_template('analytics.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')

@app.route('/profile')
def profile():
    return render_template('profile.html')

@app.route("/api/soil", methods=["GET"])
def get_soil():
    return jsonify({"soil_moisture": soil_value})

@app.route("/api/servo", methods=["POST"])
def control_servo():
    action = request.json.get("action")
    if not action:
        return jsonify({"message":"No action specified"}),400

    def send_commands():
        client.publish(TOPIC_COMMAND, action)
        print(f"📡 Command sent: {action}")
        if action == "servo_down":
            time.sleep(30)
            client.publish(TOPIC_COMMAND, "servo_up")
            print("📡 Command sent: servo_up")

    threading.Thread(target=send_commands).start()
    return jsonify({"message":f"Command '{action}' sent!"})

@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    weather = get_weather_data()
    motor_prediction = predict_motor(
        soil=soil_value,
        humidity=weather["humidity"],
        temp=weather["temperature"],
        rainfall=weather["rainfall"]
    )
    data = {
        "avg_temperature": weather["temperature"],
        "humidity": weather["humidity"],
        "rainfall": weather["rainfall"],
        "motor_prediction": motor_prediction
    }
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
