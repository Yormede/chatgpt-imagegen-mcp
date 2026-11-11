# Prompt d'utilisation : MCP imagegen unifié

À coller dans les instructions d'un agent (Claude Desktop, Codex, etc.) qui doit générer des images.

## Outils

1. `generate_image`
   - `prompt` (obligatoire) : description complète et autonome.
   - `backend` : `codex` (défaut), `francestudent`, `auto`.
   - `references` : 0 à 5 chemins locaux, backend codex uniquement.
   - `count` : 1 à 4, francestudent uniquement.
   - `aspect` : `auto|square|landscape|portrait` (francestudent).
   - `agent` : agent France Student, défaut `gpt-6-sol`.
   - `output_path` : fichier PNG (codex) ou dossier (francestudent).
   - `return_image` : `true` pour renvoyer l'image dans la réponse MCP.
2. `imagegen_quota` : quota Codex (`used_percent`, reset) + budget France Student (`chatBudget.usedPercent`, `resetAt`, rate limits).
3. `imagegen_status` : routeur Codex, jeton ChatGPT, présence du MCP France Student.
4. `francestudent_agents` : liste des agents France Student.

## Règles de choix

- Image à produire à partir de références (personnage, décor, case de BD) : `backend=codex`.
- Image texte vers image, sans référence, ou quota Codex épuisé : `backend=francestudent`.
- `backend=auto` : codex d'abord, bascule sur France Student en cas de 401/429/5xx.
- Le backend `francestudent` n'accepte pas d'image en entrée et ne garantit ni la taille ni le format exact (souvent 1254x1254, 1536x1024 ou 1024x1536).
- Un appel `francestudent` avec `count=4` génère quatre images l'une après l'autre (~20-30 s chacune).

## Quotas

- Vérifier `imagegen_quota` avant un gros lot.
- Codex : environ 80 images par fenêtre de 24 h (plan Plus, `imagegen_premium`).
- France Student : `chatBudget.usedPercent` ; s'arrêter si > 90 %.
- Erreurs France Student : `NO_SESSION`, `SESSION_EXPIRED` (lancer `npm run login` dans `francestudent-mcp`), `RATE_LIMIT` (attendre), `AD_VIEW_REQUIRED` (débloquer sur ia.francestudent.org), `NO_IMAGE` (reformuler une fois), `CF_BLOCKED` (réseau).

## Exemples

```json
{"prompt": "Superman vole au-dessus de Metropolis, case de comics, 16:9",
 "backend": "codex", "references": ["1_debut.jpg", "2_fin.jpg"]}
```

```json
{"prompt": "Une icône plate de pomme rouge sur fond blanc",
 "backend": "francestudent", "aspect": "square", "count": 1}
```

`imagegen_quota` : `{"backend": "all"}`

## Test de bout en bout (11/11/2026)

- `imagegen_quota` : codex 7 %, France Student 1,2 % (98,8 % restant).
- `generate_image backend=codex` : PNG 1536x1024, 7,8 s.
- `generate_image backend=francestudent` : PNG 1254x1254, ~25 s.
- `return_image=true` : blocs `text` + `image` reçus.
- `francestudent_agents` : 12 agents listés, 8 OpenAI compatibles image.

## Claude Desktop

Redémarrer Claude Desktop après mise à jour du serveur. Le serveur `francestudent-mcp` est lancé automatiquement en stdio par `mcp_server.py` : pas besoin de l'ajouter séparément.