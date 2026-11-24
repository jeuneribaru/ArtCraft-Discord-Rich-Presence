# ArtCraft Discord Rich Presence

Utilitaire léger pour Windows qui affiche automatiquement ta **Discord Rich Presence** lorsque tu utilises les applications de la suite **ArtCraft**.

## Fonctionnalités

- **11 applications supportées** : `PDFCraft`, `PhotoCraft`, `LightCraft`, `FilmCraft`, `DesignCraft`, `EffectCraft`, `FigCraft`, `MaterialCraft`, `SoundCraft`, `TextureCraft`, `VectorCraft`.
- **Détection intelligente** :
  - Extrait le nom du fichier actif depuis le titre de la fenêtre (ou du dossier projet parent pour `FilmCraft`).
  - **Anti-clignotement (Alt-Tab)** : conserve la présence active tant que l'application ArtCraft tourne en arrière-plan, bascule instantanément vers une autre app ArtCraft si elle prend le focus, et nettoie la présence (`clear`) à la fermeture.
  - **Chronomètre de session** conservé tant que tu restes sur la même application.
  - **Résilient** : se reconnecte automatiquement en arrière-plan si Discord est fermé ou redémarre.
- **Discret dans la barre des tâches** (`pystray`) avec menu clic droit :
  - Statut en direct (`Active: ...`, `Idle`, `Paused`, `Waiting for Discord`)
  - `Pause / Resume`
  - `Privacy Mode` (remplace le nom du fichier par `Working on a file`)
  - `Run at Startup` (active/désactive le lancement automatique au démarrage de Windows)
  - `Quit`
- **Exécutable autonome** (`ArtCraftRPC.exe`) sans console et sans Python requis.

---

## Utilisation rapide (sans Python)

1. Télécharge `ArtCraftRPC.exe` depuis les **Releases** GitHub (ou génère-le avec `.\build_exe.ps1`).
2. Place `ArtCraftRPC.exe` dans un dossier fixe (ex. `Documents\ArtCraftRPC\`).
3. Double-clique dessus, puis fais un clic droit sur l'icône **`AC`** près de l'horloge Windows (ou dans `^`) pour activer **`Run at Startup`** si tu veux qu'il démarre avec Windows.

> Assure-toi que **Discord Desktop** est ouvert et que l'option **Paramètres → Confidentialité des activités → Partager votre activité détectée avec les autres** est activée.

---

## Installation développeur (Python)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Lancer depuis les sources

```powershell
# Mode discret (sans fenêtre console, avec icône tray)
.\.venv\Scripts\pythonw.exe main.pyw

# Mode console avec logs détaillés
.\.venv\Scripts\python.exe main.py --debug

# Mode totalement masqué (sans icône tray)
.\.venv\Scripts\pythonw.exe main.pyw --headless
```

### Compiler l'exécutable `.exe`

```powershell
.\build_exe.ps1
```

Le binaire autonome est généré dans `dist\ArtCraftRPC.exe`.

### Lancer les tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

---

## Configuration & Personnalisation

`ArtCraftRPC.exe` embarque `config.json` et `apps.json` par défaut, mais tout fichier `config.json`, `apps.json` ou `icon.ico` placé **à côté** de l'exécutable surcharge automatiquement la configuration embarquée.

### `config.json`

- `DISCORD_CLIENT_ID` : ID public de l'application Discord (par défaut celui de la suite ArtCraft ; tu peux le remplacer par le tien si tu veux héberger tes propres assets).
- `small_image` / `small_text` : clé d'asset et texte du petit badge (`artcraft_logo` / `ArtCraft Suite`).
- `refresh_interval` : intervalle de vérification en secondes (défaut `3`).
- `privacy_mode` : masque les noms de fichiers au démarrage (`false` par défaut).
- `headless` : démarre sans icône system tray (`false` par défaut).
- `icon_path` : chemin vers un `.ico` personnalisé (sinon une icône `AC` est générée automatiquement).

### Ajouter une application (`apps.json`)

1. Lance l'outil de diagnostic et clique sur la fenêtre de l'application cible :
   ```powershell
   .\.venv\Scripts\python.exe debug_window.py
   ```
2. Ajoute une entrée dans `apps.json` (rechargé à chaud automatiquement sans redémarrer).

---

## Arborescence

```text
├── tests/test_artcraft_rpc.py    # Tests unitaires
├── apps.json                     # Configuration des applications supportées
├── artcraft_rpc.py               # Moteur RPC, détection Windows, system tray & startup
├── build_exe.ps1                 # Script de build PyInstaller (--onefile --noconsole)
├── config.json                   # Configuration globale
├── debug_window.py               # Outil de diagnostic du processus et titre actif
├── LICENSE                       # Licence MIT
├── main.py                       # Point d'entrée console (--debug / --headless)
├── main.pyw                      # Point d'entrée silencieux Windows
├── README.md
└── requirements.txt
```
