from flask import Flask, request, jsonify
app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify(status='ok', note='Piper TTS placeholder — Stage 9')

@app.route('/synthesize', methods=['POST'])
def synthesize():
    return jsonify(status='placeholder', audio=None), 501

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5500)
