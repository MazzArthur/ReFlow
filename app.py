import os
import subprocess
import threading
import queue
import requests
import streamlink
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

vod_queue = queue.Queue()
ffmpeg_process = None
status_data = {"status": "Parado"}

TWITCH_API_BASE = "https://api.twitch.tv/helix"

def get_app_access_token(client_id, client_secret):
    resp = requests.post(
        'https://id.twitch.tv/oauth2/token',
        params={
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'client_credentials'
        }
    )
    return resp.json().get('access_token')

def get_user_id(username, headers):
    resp = requests.get(f"{TWITCH_API_BASE}/users", params={"login": username}, headers=headers)
    return resp.json()['data'][0]['id']

def get_vods(user_id, headers):
    resp = requests.get(f"{TWITCH_API_BASE}/videos", params={"user_id": user_id, "type": "archive"}, headers=headers)
    return resp.json().get('data', [])

def get_m3u8_url(vod_url, quality):
    streams = streamlink.streams(vod_url)
    return streams[quality].url if quality in streams else streams.get('best', {}).get('url')

@app.route('/')
def login():
    return render_template("login.html")

@app.route('/login', methods=['POST'])
def do_login():
    client_id = request.form['client_id']
    client_secret = request.form['client_secret']
    username = request.form['username']
    stream_key = request.form['stream_key']

    token = get_app_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}", "Client-ID": client_id}
    user_id = get_user_id(username, headers)
    vods = get_vods(user_id, headers)

    session['client_id'] = client_id
    session['client_secret'] = client_secret
    session['username'] = username
    session['stream_key'] = stream_key
    session['vods'] = vods

    return redirect(url_for('index'))

@app.route('/home')
def index():
    username = session.get('username')
    stream_key = session.get('stream_key')
    vods = session.get('vods')
    return render_template("index.html", username=username, stream_key=stream_key, vods=vods, status=status_data)

@app.route('/iniciar', methods=['POST'])
def iniciar():
    global ffmpeg_process
    vod_urls = request.form.getlist('vod_urls')
    quality = request.form.get('quality', 'best')
    stream_key = session.get('stream_key')

    while not vod_queue.empty():
        vod_queue.get()

    for url in vod_urls:
        m3u8_url = get_m3u8_url(url, quality)
        if m3u8_url:
            vod_queue.put(m3u8_url)

    if ffmpeg_process and ffmpeg_process.poll() is None:
        return "Transmissão já está em andamento.", 400

    threading.Thread(target=stream_vods, args=(stream_key,), daemon=True).start()
    return redirect(url_for('index'))

@app.route('/encerrar', methods=['POST'])
def encerrar():
    global ffmpeg_process
    if ffmpeg_process:
        ffmpeg_process.terminate()
        ffmpeg_process = None
        status_data['status'] = 'Parado'
    return '', 204

def stream_vods(stream_key):
    global ffmpeg_process
    rtmp_url = f"rtmp://live.twitch.tv/app/{stream_key}"
    status_data['status'] = 'Transmitindo'

    ffmpeg_process = subprocess.Popen(
        ["ffmpeg", "-re", "-i", "-", "-c:v", "copy", "-c:a", "aac", "-f", "flv", rtmp_url],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        bufsize=0
    )

    while not vod_queue.empty():
        vod_url = vod_queue.get()
        decode_proc = subprocess.Popen(
            ["ffmpeg", "-i", vod_url, "-f", "mpegts", "-c:v", "copy", "-c:a", "aac", "-"],
            stdout=subprocess.PIPE
        )
        while True:
            chunk = decode_proc.stdout.read(4096)
            if not chunk:
                break
            try:
                ffmpeg_process.stdin.write(chunk)
            except BrokenPipeError:
                break
        decode_proc.wait()

    ffmpeg_process.stdin.close()
    ffmpeg_process.wait()
    status_data['status'] = 'Parado'
    ffmpeg_process = None

if __name__ == '__main__':
    app.run(debug=True, port=5000)
