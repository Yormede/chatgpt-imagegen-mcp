# chatgpt-imagegen-mcp

Serveur MCP (stdio, Python ≥ 3.10, sans dépendance pour le serveur) qui génère des images depuis
Claude Desktop, Codex ou tout client MCP, avec **deux backends** :

| Backend | Source | Entrée | Particularités |
|---|---|---|---|
| `codex` (défaut) | ton abonnement ChatGPT, via le routeur local [codex-router](https://github.com/Yormede/codex-router) | texte + **5 images de référence max** | modèle `gpt-image-2`, quota `imagegen_premium` (~80 images / 24 h en plan Plus) |
| `francestudent` | ton compte France Student IA, via [francestudent-mcp](https://github.com/Yormede/francestudent-mcp) | texte seul, 1 à 4 images | modèle `gpt-image-2-codex`, consomme le budget de chat France Student |
| `auto` | essaie `codex`, bascule sur `francestudent` si quota épuisé / erreur 401-5xx | | |

Aucune clé API OpenAI n'est nécessaire.

## Chaîne d'appel (backend codex)

```
Client MCP ──stdio──► mcp_server.py ──► imagegen.py
        POST http://127.0.0.1:4202/v1/images/{generations|edits}
              (Bearer = jeton ChatGPT lu dans ~/.codex/auth.json)
                                 ▼
codex-router (127.0.0.1:4202) ── relaie ──► https://chatgpt.com/backend-api/codex/images/…
                                 ▼
                   { data: [ { b64_json } ] } ──► PNG local
```

Le routeur vérifie que le jeton présenté est celui de la session Codex puis relaie la requête.

Le backend `francestudent` lance `node francestudent-mcp/src/server.mjs` comme sous-processus et lui
parle en MCP (`francestudent_client.py`).

## Installation

```powershell
git clone https://github.com/Yormede/chatgpt-imagegen-mcp
pip install -r requirements.txt        # Pillow, uniquement pour grid.py
# backend codex : Codex connecté (`codex login`) + codex-router lancé sur le port 4202
# backend francestudent : francestudent-mcp cloné à côté (ou FS_MCP_SERVER) et `npm run login` fait
```

Claude Desktop (`claude_desktop_config.json`) :

```json
"imagegen": {
  "command": "python",
  "args": ["C:\\chemin\\vers\\chatgpt-imagegen-mcp\\mcp_server.py"]
}
```

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `IMAGEGEN_ROUTER` | `http://127.0.0.1:4202` | adresse du routeur Codex |
| `CODEX_HOME` | `~/.codex` | dossier contenant `auth.json` |
| `IMAGEGEN_OUT_DIR` | `~/Downloads` | sortie par défaut (codex) |
| `FS_MCP_SERVER` | `../francestudent-mcp/src/server.mjs` | serveur France Student |
| `FS_NODE` | `node` du PATH | exécutable Node |
| `FS_OUTPUT_DIR` | `~/Pictures/francestudent` | sortie par défaut (francestudent) |

## Outils MCP

| Outil | Rôle |
|---|---|
| `generate_image` | `prompt`, `backend` (codex/francestudent/auto), `references` (≤ 5, codex), `output_path`, `size`, `quality`, `background`, `count` (francestudent), `aspect`, `agent`, `return_image` |
| `imagegen_quota` | quota des deux backends |
| `imagegen_status` | santé du routeur, expiration du jeton ChatGPT, présence du MCP France Student |
| `francestudent_agents` | agents France Student disponibles |

## CLI

```powershell
python imagegen.py --health
python imagegen.py --prompt "Une pomme rouge sur fond blanc" --out pomme.png
python imagegen.py --prompt-file prompt.txt --ref a.jpg --ref b.jpg --out resultat.png
python quota.py                     # état du quota codex (state/)
python grid.py --prompt-file p.txt --cols 2 --rows 2 --name test --out-dir sorties   # planche N cases puis découpe
```

## Limites mesurées (backend codex, novembre 2026)

- **5 images de référence maximum** par appel (erreur 400 au-delà) ; 1 image de sortie par appel.
- `size=auto` : 1672×941 observé en 16:9, 1312×1199 en carré. Qualité `low` en génération simple,
  `medium` avec références. 16 s sans référence, 55–65 s avec 5 références.
- **Quota image séparé du quota modèle Codex** ; en-têtes `x-codex-primary-used-percent`,
  `x-codex-primary-reset-at`, `x-codex-plan-type`, enregistrés dans `state/`. Un dépassement renvoie
  **429** avec `resets_at` ; le client s'arrête sans réessayer.
- **Modération** à l'entrée et à la sortie (`400 moderation_blocked`), non déterministe : une nouvelle
  tentative identique peut passer.

## Dépannage

| Symptôme | Cause | Action |
|---|---|---|
| 401 `native OpenAI route requires an active ChatGPT/Codex session` | jeton absent/expiré | `codex login status`, puis relancer |
| 400 `moderation_blocked` | modération | réessayer, puis reformuler |
| 429 | quota image | attendre `resets_at` ou `backend=auto` |
| routeur injoignable | codex-router arrêté | démarrer le routeur |
| `SERVER_MISSING` / `SESSION_EXPIRED` (francestudent) | MCP France Student absent ou session expirée | vérifier `FS_MCP_SERVER`, `npm run login` |

## Avertissement

Projet personnel non officiel, sans lien avec OpenAI ni France Student. Il utilise **tes propres**
sessions (ChatGPT, France Student) pour un usage personnel : ne partage pas `auth.json` ni
`session.json`, n'expose pas le port 4202, ne revends pas l'accès, et respecte les conditions
d'utilisation de chaque service. Les quotas sont utilisés, pas contournés.

## Licence

MIT
