#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import threading
import csv
import os
from datetime import datetime

import RPi.GPIO as GPIO
from flask import Flask, Response, render_template_string, redirect, url_for, send_file
import json

###############################################################################
# Configuration GPIO et capteur
###############################################################################
BROCHE_CAPTEUR = 17
DELAI_ARRET = 10.0

GPIO.setmode(GPIO.BCM)
GPIO.setup(BROCHE_CAPTEUR, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

###############################################################################
# Variables globales
###############################################################################
data_lock = threading.Lock()

data = {
    "ligne_active": False,
    "temps_demarrage": 0.0,
    "temps_ouverture": 0.0,
    "nombre_arrets": 0,
    "derniere_detection": None
}

HISTORIQUE_CSV = "historique.csv"

###############################################################################
# Thread de surveillance du capteur
###############################################################################
def surveillance_capteur():
    while True:
        etat_capteur = GPIO.input(BROCHE_CAPTEUR)  # 1 = détecte, 0 = pas de détection
        maintenant = time.time()

        with data_lock:
            if data["ligne_active"]:
                if etat_capteur == 1:
                    data["derniere_detection"] = maintenant
                else:
                    if data["derniere_detection"] is not None:
                        ecart = maintenant - data["derniere_detection"]
                        if ecart >= DELAI_ARRET:
                            data["nombre_arrets"] += 1
                            data["derniere_detection"] = None
        time.sleep(0.1)

###############################################################################
# Application Flask (SSE pour la supervision en temps réel)
###############################################################################
app = Flask(__name__)

# On intègre du HTML + CSS dans un template unique
page_html = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Supervision de la ligne</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0; 
            padding: 0;
            background: #f0f0f0; 
        }
        .container {
            max-width: 700px;
            margin: 40px auto; 
            background: #ffffff;
            padding: 20px; 
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .stats {
            margin-top: 20px;
            line-height: 1.6;
        }
        .stats p {
            margin: 5px 0;
            font-weight: bold;
        }
        .button-container {
            margin-top: 20px;
            text-align: center;
        }
        form, a {
            display: inline-block;
            margin: 0 5px;
        }
        button {
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 15px;
        }
        .btn-start {
            background-color: #4CAF50; 
            color: #fff;
        }
        .btn-stop {
            background-color: #f44336; 
            color: #fff;
        }
        .btn-download {
            background-color: #2196F3; 
            color: #fff;
        }
        button:hover {
            opacity: 0.9;
        }
        /* Pour le footer, si besoin */
        .footer {
            margin-top: 30px;
            text-align: center;
            color: #777;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Supervision de la ligne</h1>

        <div class="stats">
            <p>Ligne active : <span id="ligne_active"></span></p>
            <p>Temps d'ouverture (secondes) : <span id="temps_ouverture"></span></p>
            <p>Nombre d'arrêts : <span id="nombre_arrets"></span></p>
            <p>Ratio (temps_ouverture / nombre_arrets) : <span id="ratio"></span></p>
        </div>

        <div class="button-container">
            {% if not ligne_active %}
            <form action="{{ url_for('demarrer_ligne') }}" method="post">
                <button type="submit" class="btn-start">Démarrer la ligne</button>
            </form>
            {% else %}
            <form action="{{ url_for('arreter_ligne') }}" method="post">
                <button type="submit" class="btn-stop">Arrêter la ligne</button>
            </form>
            {% endif %}

            <a href="{{ url_for('telecharger_historique') }}">
                <button class="btn-download">Télécharger l'historique CSV</button>
            </a>
        </div>
        <div class="footer">
            <p>MDLZ Supervision &copy; 2025</p>
        </div>
    </div>

    <script>
      // Connexion SSE
      var source = new EventSource("/stream");
      source.onmessage = function(event) {
          var info = JSON.parse(event.data);

          document.getElementById("ligne_active").textContent = info.ligne_active;
          document.getElementById("temps_ouverture").textContent = info.temps_ouverture;
          document.getElementById("nombre_arrets").textContent = info.nombre_arrets;
          document.getElementById("ratio").textContent = info.ratio;
      };
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    with data_lock:
        return render_template_string(
            page_html,
            ligne_active=data["ligne_active"]
        )

@app.route("/stream")
def stream():
    """
    Flux SSE actualisé toutes les 1 seconde.
    """
    def event_stream():
        while True:
            with data_lock:
                temps_ouverture = data["temps_ouverture"]
                if data["ligne_active"]:
                    temps_ouverture = time.time() - data["temps_demarrage"]

                if data["nombre_arrets"] > 0:
                    ratio_val = round(temps_ouverture / data["nombre_arrets"], 2)
                else:
                    ratio_val = 0.0

                infos = {
                    "ligne_active": data["ligne_active"],
                    "temps_ouverture": round(temps_ouverture, 2),
                    "nombre_arrets": data["nombre_arrets"],
                    "ratio": ratio_val
                }

            message = json.dumps(infos)
            yield f"data: {message}\n\n"
            time.sleep(1)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/demarrer", methods=["POST"])
def demarrer_ligne():
    with data_lock:
        if not data["ligne_active"]:
            # Réinitialisation
            data["ligne_active"] = True
            data["temps_demarrage"] = time.time()
            data["temps_ouverture"] = 0.0
            data["nombre_arrets"] = 0
            data["derniere_detection"] = None
    return redirect(url_for("index"))

@app.route("/arreter", methods=["POST"])
def arreter_ligne():
    with data_lock:
        if data["ligne_active"]:
            final_ouverture = time.time() - data["temps_demarrage"]
            data["temps_ouverture"] = final_ouverture

            if data["nombre_arrets"] > 0:
                ratio_final = round(final_ouverture / data["nombre_arrets"], 2)
            else:
                ratio_final = 0.0

            sauvegarder_donnees(
                temps_ouverture=round(final_ouverture, 2),
                nombre_arrets=data["nombre_arrets"],
                ratio=ratio_final
            )

            # Remettre à zéro
            data["ligne_active"] = False
            data["temps_demarrage"] = 0.0
            data["temps_ouverture"] = 0.0
            data["nombre_arrets"] = 0
            data["derniere_detection"] = None

    return redirect(url_for("index"))

@app.route("/telecharger")
def telecharger_historique():
    if os.path.isfile(HISTORIQUE_CSV):
        return send_file(
            HISTORIQUE_CSV,
            as_attachment=True,
            download_name="historique.csv"
        )
    else:
        return "Aucun fichier historique à télécharger.", 404

def sauvegarder_donnees(temps_ouverture, nombre_arrets, ratio):
    fichier_existe = os.path.isfile(HISTORIQUE_CSV)

    with open(HISTORIQUE_CSV, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter=';')
        if not fichier_existe:
            writer.writerow(["Horodatage", "Temps_ouverture_s", "Nombre_arrets", "Ratio"])

        horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([horodatage, temps_ouverture, nombre_arrets, ratio])

###############################################################################
# Programme principal
###############################################################################
if __name__ == "__main__":
    thread_capteur = threading.Thread(target=surveillance_capteur, daemon=True)
    thread_capteur.start()

    app.run(host="0.0.0.0", port=5000, debug=False)

