# Snapchat Memories Processor

Outil en ligne de commande pour traiter vos **Snapchat Memories** exportées, entièrement en local.

Snapchat permet de [demander une copie de vos données](https://accounts.snapchat.com/accounts/downloadmydata), qui arrive sous forme de fichiers ZIP contenant vos photos, vidéos et métadonnées. Cet outil prend ces ZIPs et produit des fichiers propres avec :

- **Overlays appliqués** — les filtres/textes Snapchat sont fusionnés sur les photos et vidéos
- **Métadonnées EXIF** — date de prise de vue et coordonnées GPS intégrées dans chaque fichier
- **Nommage clair** — fichiers renommés au format `AAAA-MM-JJ-HHMMSS.ext`
- **Horodatage fichier** — la date de modification correspond à la date de la memory

## Pourquoi cet outil ?

L'export Snapchat fournit les médias bruts avec des noms UUID et les overlays dans des fichiers PNG séparés. Les métadonnées (date, GPS) ne sont disponibles que dans un fichier JSON. Cet outil rassemble tout ça automatiquement.

> **Note :** Il existe des outils comme [MemorEasy](https://github.com/bransoned/MemorEasy) qui téléchargent les memories via l'API Snapchat. Cependant, les memories antérieures à ~avril 2022 retournent souvent une erreur HTTP 403 côté serveur. Cet outil contourne le problème en travaillant directement avec les fichiers ZIP déjà téléchargés.

## Prérequis

- **Python 3.9+**
- **ffmpeg** — inclus automatiquement via le package `moviepy`, ou installable séparément

> **Alternative :** un exécutable standalone (`SnapchatMemories.exe`) est disponible dans les [Releases](https://github.com/SimonCtl/snapchat-memories-processor/releases). Aucune installation requise — il suffit de le placer à côté du dossier `exports/` et de le lancer.

## Installation

```bash
git clone https://github.com/SimonCtl/snapchat-memories-processor.git
cd snapchat-memories-processor
pip install -r requirements.txt
```

## Utilisation

### 1. Récupérer vos données Snapchat

1. Allez sur [accounts.snapchat.com](https://accounts.snapchat.com/accounts/downloadmydata)
2. Demandez un export complet de vos données
3. Téléchargez les fichiers ZIP reçus par email

### 2. Préparer les fichiers

Placez tous les fichiers ZIP dans le dossier `exports/` (dossier à créer) :

```
snapchat-memories-processor/
├── process_memories.py
├── requirements.txt
└── exports/
    ├── mydata~1234567890.zip
    ├── mydata~1234567890-2.zip
    ├── mydata~1234567890-3.zip
    └── ...
```

### 3. Lancer le traitement

```bash
python process_memories.py
```

Le script effectue 4 étapes :

1. **Extraction** — décompresse les ZIPs et rassemble les médias
2. **Lecture du JSON** — parse `memories_history.json` pour les métadonnées
3. **Indexation** — associe chaque entrée JSON à ses fichiers (main + overlay)
4. **Traitement** — applique les overlays, écrit les EXIF, renomme les fichiers

### 4. Résultat

Les fichiers traités sont dans le dossier `sortie/` :

```
sortie/
├── 2019-06-15-103819.jpg
├── 2020-12-25-181222.jpg
├── 2023-08-07-135612.mp4
└── ...
```

## Structure des dossiers

| Dossier     | Description |
|-------------|-------------|
| `exports/`  | Fichiers ZIP d'export Snapchat (entrée) |
| `_temp/`    | Dossier temporaire d'extraction (supprimé après traitement) |
| `sortie/`   | Memories traitées avec overlays et métadonnées (résultat) |

## Fonctionnement technique

### Photos (JPG)
- L'overlay PNG est superposé via **Pillow** (alpha compositing)
- Les métadonnées EXIF (date, GPS) sont écrites avec **piexif**

### Vidéos (MP4)
- L'overlay PNG est superposé via **ffmpeg** (filtre `overlay`)
- La rotation vidéo (displaymatrix) est détectée et corrigée automatiquement — les vidéos portrait stockées en paysage sont transposées avant l'application de l'overlay
- Les métadonnées (date, GPS) sont ajoutées via **ffmpeg** en copie de flux (sans ré-encodage quand il n'y a pas d'overlay)

### Correspondance fichiers ↔ JSON
Les fichiers dans l'export sont nommés `AAAA-MM-JJ_UUID-main.ext`. Le JSON contient une URL avec un paramètre `mid` qui correspond à cet UUID. C'est cette correspondance qui permet d'associer les métadonnées aux bons fichiers.

## Licence

MIT
