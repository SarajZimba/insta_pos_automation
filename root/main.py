# from flask import Flask, request, Blueprint
# app = Flask(__name__)
# import mysql.connector
# from datetime import datetime

# from flask_cors import CORS, cross_origin
# CORS(app)
# app.config['CORS_HEADERS'] = 'Content-Type'
# app.config['SECRET_KEY'] = 'secret!'
# import os
# from dotenv import load_dotenv
# load_dotenv()
# from flask_socketio import SocketIO, emit,join_room,leave_room
# socketio = SocketIO(app, cors_allowed_origins="*",monitor_clients=True,async_mode='eventlet',allow_upgrades=False)
# import jwt
# import pytz

# from root.insta_routes.insta_receive import instagram_receive

# # app.register_blueprint(whatsapp_bp)
# # app.register_blueprint(webhook_bp)

# app.register_blueprint(instagram_receive)

# @app.route("/")
# def index():
#     return "working"



# changes
from flask import Flask, request, Blueprint
from flask_cors import CORS, cross_origin
import os
from dotenv import load_dotenv
import datetime

app = Flask(__name__)
CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'
load_dotenv()

from root.insta_routes.insta_receive import instagram_receive


app.register_blueprint(instagram_receive)

@app.route("/")
def index():
    return " it is working"

if __name__ == "__main__":
    app.run(debug=True)



