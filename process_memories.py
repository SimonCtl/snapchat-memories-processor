"""
Snapchat Memories Processor

Traite les exports de données Snapchat (fichiers ZIP) en local :
  1. Extrait les médias et le JSON depuis les ZIPs
  2. Applique les overlays Snapchat (PNG sur JPG/MP4)
  3. Écrit les métadonnées EXIF (date, GPS) dans les fichiers
  4. Renomme les fichiers au format AAAA-MM-JJ-HHMMSS

Utilisation :
  1. Placez vos fichiers ZIP d'export Snapchat dans le dossier ./exports/
  2. Lancez : python process_memories.py
  3. Retrouvez vos memories dans ./sortie/

Dépendances : Pillow, piexif, moviepy (pour ffmpeg embarqué)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from PIL import Image
import piexif


# ==========================================================================
#  Configuration
# ==========================================================================

DOSSIER_EXPORTS = Path("./exports")    # ZIPs d'export Snapchat à traiter
DOSSIER_TEMP    = Path("./_temp")      # Dossier temporaire d'extraction
DOSSIER_SORTIE  = Path("./sortie")     # Résultat final
JSON_CHEMIN     = "json/memories_history.json"


# ==========================================================================
#  Encodage — forcer UTF-8 sur Windows pour éviter les problèmes de console
# ==========================================================================

def _configurer_encodage():
    """Force la sortie console en UTF-8 (évite le UTF-16 LE de PowerShell)."""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass  # Python < 3.7


# ==========================================================================
#  ffmpeg
# ==========================================================================

def trouver_ffmpeg():
    """Cherche ffmpeg : d'abord via imageio_ffmpeg, sinon dans le PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    chemin = shutil.which("ffmpeg")
    if chemin:
        return chemin

    print("ERREUR : ffmpeg introuvable.")
    print("  Installez-le via : pip install moviepy")
    sys.exit(1)


# ==========================================================================
#  Extraction des ZIPs
# ==========================================================================

def extraire_tous_les_zips(dossier_exports, dossier_temp):
    """Extrait tous les ZIPs et rassemble les médias + le JSON."""
    zips = sorted(dossier_exports.glob("*.zip"))
    if not zips:
        print(f"ERREUR : aucun fichier ZIP trouvé dans {dossier_exports}/")
        sys.exit(1)

    print(f"  {len(zips)} fichier(s) ZIP trouvé(s)")

    dossier_medias = dossier_temp / "medias"
    dossier_medias.mkdir(parents=True, exist_ok=True)
    chemin_json = None

    for i, fichier_zip in enumerate(zips):
        print(f"  Extraction de {fichier_zip.name}...")
        temp = dossier_temp / f"_zip_{i}"

        with zipfile.ZipFile(fichier_zip, "r") as z:
            z.extractall(temp)

        # Récupérer le JSON depuis le ZIP principal
        json_candidat = temp / JSON_CHEMIN
        if json_candidat.exists() and chemin_json is None:
            chemin_json = dossier_temp / "memories_history.json"
            shutil.move(str(json_candidat), str(chemin_json))

        # Déplacer les fichiers médias dans le dossier commun
        dossier_mem = temp / "memories"
        if dossier_mem.exists():
            for f in dossier_mem.iterdir():
                if f.is_file() and f.suffix.lower() in (".jpg", ".mp4", ".png"):
                    cible = dossier_medias / f.name
                    if not cible.exists():
                        shutil.move(str(f), str(cible))

        # Nettoyer le dossier temporaire du ZIP
        shutil.rmtree(temp, ignore_errors=True)

    if chemin_json is None or not chemin_json.exists():
        print("ERREUR : memories_history.json introuvable dans le ZIP principal")
        sys.exit(1)

    nb_fichiers = len(list(dossier_medias.glob("*")))
    print(f"  {nb_fichiers} fichiers médias extraits")

    return chemin_json, dossier_medias


# ==========================================================================
#  Lecture du JSON
# ==========================================================================

def lire_json(chemin_json):
    """Lit memories_history.json et extrait les métadonnées."""
    with open(chemin_json, "r", encoding="utf-8") as f:
        donnees = json.load(f)

    entrees = donnees.get("Saved Media", [])
    print(f"  {len(entrees)} entrées dans le JSON")

    memories = []
    for e in entrees:
        date_str = e.get("Date", "")
        localisation = e.get("Location", "")
        url = e.get("Media Download Url", "") or e.get("Download Link", "")

        # Extraire latitude / longitude
        lat, lon = None, None
        if localisation:
            m = re.search(r"([-\d.]+),\s*([-\d.]+)", localisation)
            if m:
                lat_f, lon_f = float(m.group(1)), float(m.group(2))
                if -90 <= lat_f <= 90 and -180 <= lon_f <= 180 and (lat_f != 0 or lon_f != 0):
                    lat, lon = lat_f, lon_f

        # Extraire le MID depuis l'URL pour faire correspondre avec les fichiers locaux
        mid = None
        if url:
            params = parse_qs(urlparse(url).query)
            mid_list = params.get("mid", [])
            if mid_list:
                mid = mid_list[0].lower()

        if not mid or not date_str:
            continue

        memories.append({
            "date": date_str,
            "lat": lat,
            "lon": lon,
            "mid": mid,
        })

    print(f"  {len(memories)} entrées valides avec MID")
    return memories


# ==========================================================================
#  Indexation des fichiers extraits
# ==========================================================================

def indexer_fichiers(dossier_medias):
    """Construit un index UUID → chemins des fichiers (main + overlay)."""
    index = {}

    for f in dossier_medias.iterdir():
        if not f.is_file():
            continue
        # Format : AAAA-MM-JJ_UUID-main.ext ou AAAA-MM-JJ_UUID-overlay.png
        m = re.match(
            r"\d{4}-\d{2}-\d{2}_([a-fA-F0-9-]+)-(main|overlay)\.(jpg|mp4|png)",
            f.name,
        )
        if m:
            uuid = m.group(1).lower()
            role = m.group(2)  # "main" ou "overlay"
            if uuid not in index:
                index[uuid] = {}
            index[uuid][role] = f

    return index


# ==========================================================================
#  Écriture EXIF (photos JPG)
# ==========================================================================

def _vers_dms_rationnel(degres_decimaux):
    """Convertit des degrés décimaux en format DMS rationnel EXIF."""
    d = int(abs(degres_decimaux))
    m_float = (abs(degres_decimaux) - d) * 60
    m = int(m_float)
    s_float = (m_float - m) * 60
    return ((d, 1), (m, 1), (int(s_float * 10000), 10000))


def ecrire_exif_jpg(chemin, date_str, lat, lon):
    """Écrit la date et le GPS dans les données EXIF d'un JPG."""
    try:
        exif_dict = piexif.load(str(chemin))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

    # "2025-12-09 11:10:51 UTC" → "2025:12:09 11:10:51"
    date_propre = date_str.replace(" UTC", "").strip()
    date_exif = date_propre.replace("-", ":", 2).encode("utf-8")

    exif_dict["0th"][piexif.ImageIFD.DateTime] = date_exif
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_exif
    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_exif

    if lat is not None and lon is not None:
        exif_dict["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _vers_dms_rationnel(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _vers_dms_rationnel(lon),
        }

    try:
        piexif.insert(piexif.dump(exif_dict), str(chemin))
    except Exception as e:
        print(f"    Attention : écriture EXIF échouée pour {chemin.name} : {e}")


# ==========================================================================
#  Écriture métadonnées MP4 (via ffmpeg)
# ==========================================================================

def ecrire_metadata_mp4(ffmpeg, entree, sortie, date_str, lat, lon):
    """Écrit la date et le GPS dans un MP4 via ffmpeg (copie sans ré-encodage)."""
    date_propre = date_str.replace(" UTC", "").strip()
    date_ffmpeg = date_propre.replace(" ", "T") + "Z"

    cmd = [
        ffmpeg,
        "-i", str(entree),
        "-c", "copy",
        "-metadata", f"creation_time={date_ffmpeg}",
    ]

    if lat is not None and lon is not None:
        signe_lat = "+" if lat >= 0 else ""
        signe_lon = "+" if lon >= 0 else ""
        cmd.extend(["-metadata", f"location={signe_lat}{lat}{signe_lon}{lon}/"])

    cmd.extend(["-y", str(sortie)])

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"Erreur ffmpeg (métadonnées) : {r.stderr[-200:]}")


# ==========================================================================
#  Application des overlays
# ==========================================================================

def appliquer_overlay_jpg(chemin_base, chemin_overlay, chemin_sortie):
    """Superpose un overlay PNG sur un JPG."""
    base = Image.open(chemin_base).convert("RGBA")
    overlay = Image.open(chemin_overlay).convert("RGBA")

    if base.size != overlay.size:
        overlay = overlay.resize(base.size, Image.LANCZOS)

    resultat = Image.alpha_composite(base, overlay).convert("RGB")
    resultat.save(chemin_sortie, "JPEG", quality=95)

    base.close()
    overlay.close()
    resultat.close()


def appliquer_overlay_mp4(ffmpeg, chemin_video, chemin_overlay, chemin_sortie):
    """Superpose un overlay PNG sur un MP4 via ffmpeg."""
    # Récupérer les dimensions et la rotation de la vidéo
    r = subprocess.run(
        [ffmpeg, "-i", str(chemin_video)],
        capture_output=True, text=True, timeout=30,
    )

    # Détecter la rotation (displaymatrix)
    rotation = 0
    rot_match = re.search(r"rotation of (-?\d+\.?\d*) degrees", r.stderr)
    if rot_match:
        rotation = round(float(rot_match.group(1))) % 360

    # Détecter les dimensions brutes de la vidéo
    dim = re.search(r"(\d{2,5})x(\d{2,5})", r.stderr)
    if dim:
        larg, haut = int(dim.group(1)), int(dim.group(2))

        # Dimensions d'affichage (après rotation)
        if rotation in (90, 270):
            larg_affich, haut_affich = haut, larg
        else:
            larg_affich, haut_affich = larg, haut

        # Redimensionner l'overlay aux dimensions d'affichage
        overlay = Image.open(chemin_overlay)
        if overlay.size != (larg_affich, haut_affich):
            overlay = overlay.resize((larg_affich, haut_affich), Image.LANCZOS)
            overlay.save(chemin_overlay, "PNG")
        overlay.close()

    # Construire la chaîne de filtres (rotation manuelle + overlay)
    if rotation == 270:
        filtre = "[0:v]transpose=1[v];[v][1:v]overlay=0:0"
    elif rotation == 90:
        filtre = "[0:v]transpose=2[v];[v][1:v]overlay=0:0"
    elif rotation == 180:
        filtre = "[0:v]hflip,vflip[v];[v][1:v]overlay=0:0"
    else:
        filtre = "[0:v][1:v]overlay=0:0"

    cmd = [
        ffmpeg,
        "-display_rotation:v:0", "0",
        "-noautorotate",
        "-i", str(chemin_video),
        "-i", str(chemin_overlay),
        "-filter_complex", filtre,
        "-codec:a", "copy",
        "-y", str(chemin_sortie),
    ]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"Erreur ffmpeg (overlay) : {r.stderr[-300:]}")


# ==========================================================================
#  Timestamp fichier
# ==========================================================================

def ajuster_timestamp_fichier(chemin, date_str):
    """Ajuste la date de modification du fichier pour correspondre à la memory."""
    date_propre = date_str.replace(" UTC", "").strip()
    try:
        dt = datetime.strptime(date_propre, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
        os.utime(chemin, (ts, ts))
    except Exception:
        pass


# ==========================================================================
#  Traitement principal
# ==========================================================================

def nom_fichier_sortie(date_str, ext):
    """Convertit 'AAAA-MM-JJ HH:MM:SS UTC' en 'AAAA-MM-JJ-HHMMSS.ext'."""
    propre = date_str.replace(" UTC", "").strip()
    nom = propre.replace(" ", "-").replace(":", "")
    return f"{nom}{ext}"


def traiter_memories(memories, index_fichiers, dossier_temp, dossier_sortie, ffmpeg):
    """Traite toutes les memories : overlay + EXIF + renommage."""
    dossier_sortie.mkdir(parents=True, exist_ok=True)

    total = len(memories)
    traites = 0
    liste_ignores = []   # (mid, date) des memories sans fichier local
    liste_erreurs = []   # (nom_fichier, message) des erreurs de traitement

    for i, mem in enumerate(memories):
        mid = mem["mid"]
        date_str = mem["date"]
        lat = mem["lat"]
        lon = mem["lon"]

        fichiers = index_fichiers.get(mid)
        if not fichiers or "main" not in fichiers:
            liste_ignores.append((mid, date_str))
            continue

        fichier_main = fichiers["main"]
        fichier_overlay = fichiers.get("overlay")
        ext = fichier_main.suffix

        nom = nom_fichier_sortie(date_str, ext)
        chemin_sortie = dossier_sortie / nom

        # Gérer les doublons (même horodatage)
        if chemin_sortie.exists():
            base = nom.rsplit(".", 1)[0]
            compteur = 2
            while chemin_sortie.exists():
                chemin_sortie = dossier_sortie / f"{base}_{compteur}{ext}"
                compteur += 1

        print(f"  [{i+1}/{total}] {nom}")

        try:
            if ext == ".jpg":
                if fichier_overlay and fichier_overlay.exists():
                    appliquer_overlay_jpg(fichier_main, fichier_overlay, chemin_sortie)
                else:
                    shutil.copy2(fichier_main, chemin_sortie)
                ecrire_exif_jpg(chemin_sortie, date_str, lat, lon)

            elif ext == ".mp4":
                if fichier_overlay and fichier_overlay.exists():
                    temp_overlay = dossier_temp / f"_overlay_temp{ext}"
                    try:
                        appliquer_overlay_mp4(ffmpeg, fichier_main, fichier_overlay, temp_overlay)
                        ecrire_metadata_mp4(ffmpeg, temp_overlay, chemin_sortie, date_str, lat, lon)
                    finally:
                        if temp_overlay.exists():
                            temp_overlay.unlink()
                else:
                    ecrire_metadata_mp4(ffmpeg, fichier_main, chemin_sortie, date_str, lat, lon)

            ajuster_timestamp_fichier(chemin_sortie, date_str)
            traites += 1

        except Exception as e:
            liste_erreurs.append((fichier_main.name, str(e)))
            print(f"    ERREUR : {fichier_main.name} : {e}")

    # Résumé
    print()
    print(f"  Traités : {traites}")
    print(f"  Ignorés : {len(liste_ignores)}")
    print(f"  Erreurs : {len(liste_erreurs)}")

    if liste_ignores:
        print(f"\n  Fichiers ignorés (MID absent des fichiers extraits) :")
        for mid, date in liste_ignores:
            print(f"    - {date}  (mid: {mid})")

    if liste_erreurs:
        print(f"\n  Fichiers en erreur :")
        for nom, msg in liste_erreurs:
            print(f"    - {nom} : {msg}")


# ==========================================================================
#  Point d'entrée
# ==========================================================================

def main():
    _configurer_encodage()

    print("=" * 60)
    print("  Snapchat Memories Processor")
    print("=" * 60)

    ffmpeg = trouver_ffmpeg()
    print(f"  ffmpeg : {ffmpeg}\n")

    dossier_medias = DOSSIER_TEMP / "medias"
    chemin_json = DOSSIER_TEMP / "memories_history.json"

    # Étape 1 — Extraction des ZIPs (sauté si déjà fait)
    print("[1/4] Extraction des ZIPs...")
    if dossier_medias.exists() and any(dossier_medias.iterdir()) and chemin_json.exists():
        nb = len(list(dossier_medias.glob("*")))
        print(f"  Déjà extrait — {nb} fichiers dans {dossier_medias}/")
    else:
        if not DOSSIER_EXPORTS.exists():
            print(f"ERREUR : dossier {DOSSIER_EXPORTS}/ introuvable")
            print("  Placez vos ZIPs d'export Snapchat dans ./exports/")
            sys.exit(1)
        DOSSIER_TEMP.mkdir(parents=True, exist_ok=True)
        chemin_json, dossier_medias = extraire_tous_les_zips(DOSSIER_EXPORTS, DOSSIER_TEMP)

    # Étape 2 — Lecture du JSON
    print("\n[2/4] Lecture du JSON...")
    memories = lire_json(chemin_json)

    # Étape 3 — Indexation des fichiers
    print("\n[3/4] Indexation des fichiers...")
    index_fichiers = indexer_fichiers(dossier_medias)
    print(f"  {len(index_fichiers)} médias indexés (par MID)")

    # Étape 4 — Traitement
    print(f"\n[4/4] Traitement des memories...")
    traiter_memories(memories, index_fichiers, DOSSIER_TEMP, DOSSIER_SORTIE, ffmpeg)

    # Nettoyage du dossier temporaire
    print("\nSuppression du dossier temporaire...")
    shutil.rmtree(DOSSIER_TEMP, ignore_errors=True)

    print(f"\nTerminé ! Vos memories sont dans ./{DOSSIER_SORTIE}/")
    input("\nAppuyez sur Entrée pour fermer...")


if __name__ == "__main__":
    main()
