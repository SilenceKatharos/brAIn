# brAIn — Vision et spécifications

## 1. Pourquoi brAIn

Les systèmes de notes existants (Obsidian, Notion, Logseq) traitent le savoir comme une **bibliothèque** : des fichiers texte que l'IA peut consulter, indexer, citer. Mais l'IA relit à chaque fois, et chaque relecture coûte des tokens et de la cohérence.

brAIn change l'angle : on ne stocke pas le texte, on stocke **la structure causale** que le texte décrit. Un document est un matériau brut dont on extrait des assertions atomiques avant de le mettre de côté. Ce qui reste dans le graphe est compressé, traçable, et interrogeable sans jamais relire la source.

Le projet est généraliste : n'importe quel domaine, stocké dans **Kuzu** (graph DB embarquée, Cypher), avec **Claude** comme extracteur et comme interlocuteur.

---

## 2. Convictions fondatrices

1. **Le graphe est l'asset, pas le code.** Le code reste simple et remplaçable ; le graphe se bonifie avec le temps.
2. **Une assertion vaut mieux qu'un paragraphe.** Mieux vaut `ttl_trop_court --causes--> cache_miss_frequent` (+ une `evidence` d'une phrase) que trois paragraphes à reparcourir.
3. **La causalité est première.** Les relations taxonomiques (`is_a`, `part_of`) sont auxiliaires. Le cœur, ce sont `causes / prevents / requires / enables`.
4. **Le document source est jetable.** Si l'extraction est correcte, on doit pouvoir effacer le doc et continuer à raisonner. Si on n'y arrive pas, l'extraction est incomplète.
5. **Traçabilité par défaut.** Chaque nœud et arête porte `sources` (doc_id d'origine) et `evidence` (citation ou justification courte).
6. **La retrieval est à la demande, pas préventive.** Le graphe est la mémoire longue durée. Le contexte de Claude est la mémoire courte. On ne charge jamais tout le graphe en contexte — on récupère uniquement ce dont on a besoin, au moment où on en a besoin.

---

## 3. Ce que brAIn n'est PAS

- Pas un système de prise de notes : on injecte, on n'écrit pas dedans.
- Pas un RAG textuel : on stocke la structure causale, pas des chunks de texte.
- Pas un wiki : l'unité de consultation est le sous-graphe, pas la page.
- Pas une mémoire de Claude : la mémoire conversationnelle reste dans `~/.claude/.../memory/`. brAIn modélise un **domaine de connaissance**, pas des préférences personnelles.
- Pas un moteur de recherche : on traverse, on raisonne, on infère — on ne cherche pas des mots-clés.

---

## 4. Architecture cible

### 4.1 Vue d'ensemble

```
┌─────────────────────────────────────────────────────┐
│                   React Frontend                     │
│   Ingestion view  │  Query view  │  Graph explorer   │
└───────────────────┬─────────────┬───────────────────┘
                    │             │
              ┌─────▼─────────────▼──────┐
              │        Backend API        │
              │   (FastAPI, streaming)    │
              └─────────────┬────────────┘
                            │ tool calls
              ┌─────────────▼────────────┐
              │    Claude (tool use)      │
              │  query_graph  get_node    │
              │  traverse     cypher      │
              └─────────────┬────────────┘
                            │ Kuzu Python binding
              ┌─────────────▼────────────┐
              │      Kuzu graph DB        │
              │   graph/kuzu_db/          │
              └──────────────────────────┘
```

### 4.2 Le modèle mémoire

Le contexte de Claude est la **mémoire de travail** (court terme) : il ne contient que ce qui est actif dans la conversation. Le graphe Kuzu est la **mémoire longue durée** : persistante, dense, structurée.

Le lien entre les deux se fait par les **tool calls** : quand Claude a besoin d'un concept pendant une extraction ou une réponse, il l'appelle nommément. Il ne charge jamais tout le graphe — il récupère le voisinage exact du concept qu'il traite à cet instant.

C'est un retrieval piloté par le besoin réel, pas une injection heuristique préventive.

### 4.3 Outils exposés à Claude

| Outil | Arguments | Résultat |
|-------|-----------|---------|
| `query_graph` | `topic: str` | Nœuds correspondants + neighborhood 1-hop |
| `get_node` | `id: str` | Nœud complet + neighborhood configurable |
| `traverse` | `from_id, rel_type, direction, depth` | Chemin dans le graphe |
| `cypher` | `query: str` | Résultat brut d'une requête Cypher |

### 4.4 Moteur de graphe

- **Kuzu** embarqué, requêtes Cypher, stockage local dans `graph/kuzu_db/`.
- Aucun serveur, aucun cloud.
- Accès via le binding Python officiel.
- CLI `brain.py` maintenu en parallèle pour les opérations bas niveau.

---

## 5. Schéma du graphe

Stratégie : **un type de nœud, un type d'arête**, discriminés par un champ `type`.

```cypher
CREATE NODE TABLE Node (
    id          STRING,           -- slug stable : "redis_cache"
    label       STRING,           -- libellé humain
    type        STRING,           -- voir vocabulaire §6
    description STRING,           -- paragraphe complet : mécanismes, exemples, contexte historique, variantes
    importance  DOUBLE DEFAULT 0.5,
    created_at  STRING,
    updated_at  STRING,
    sources     STRING[],         -- doc_id d'origine
    PRIMARY KEY (id)
);

CREATE REL TABLE Rel (
    FROM Node TO Node,
    type        STRING,           -- voir vocabulaire §6
    confidence  DOUBLE DEFAULT 0.8,
    evidences   STRING[],         -- tableau parallèle à sources
    sources     STRING[],         -- evidences[i] vient de sources[i]
    created_at  STRING,
    updated_at  STRING
);
```

Dédup d'arêtes : au plus **une arête par triplet (src, dst, type)**. Plusieurs documents qui affirment la même relation accumulent leurs evidences et doc_ids dans les tableaux parallèles. `confidence` = max observé.

---

## 6. Vocabulaires

### Types de nœuds — vocabulaire ouvert

Les types de nœuds sont un **vocabulaire de référence**, pas une contrainte dure. Le LLM peut utiliser n'importe quel type : si le type est connu, il est accepté silencieusement ; s'il est nouveau, le nœud est accepté et l'événement est loggé dans `extension_requests.jsonl` pour revue humaine.

La cohérence ne vient pas du whitelist mais du **lookup avant création** : avant de créer un nœud, le LLM consulte le graphe via `query_graph`. S'il existe déjà un nœud sémantiquement équivalent, il s'y rattache — peu importe le type qu'il aurait choisi en isolation.

Types de référence courants :

| Type | Quand l'utiliser |
|------|------------------|
| `concept` | Idée abstraite (ex : « cohérence éventuelle »). |
| `entity` | Chose nommée et identifiable (ex : « PostgreSQL »). |
| `event` | Quelque chose qui se produit dans le temps. |
| `claim` | Affirmation discutable. |
| `mechanism` | Processus qui transforme une cause en effet. |
| `algorithm` | Procédure de calcul nommée (ex : « backpropagation »). |
| `property` | Propriété quantifiable ou qualifiable. |
| `person` | Acteur humain. |
| `place` | Lieu. |
| `artifact` | Objet produit (code, document, système…). |
| `process` | Suite d'étapes orientée vers un objectif. |

### Types de relations — whitelist stricte

Les relations sont le cœur sémantique du graphe. Si le LLM invente `triggers` ou `leads_to` à la place de `causes`/`enables`, les traversées `brain.py causes X` et `brain.py effects Y` deviennent inutilisables. Les relations **ne sont pas négociables**.

Tout type de relation hors whitelist est rejeté à l'ingestion et loggé dans `extension_requests.jsonl`.

**Relations causales (cœur)**

| Relation | Sémantique |
|----------|------------|
| `causes` | A provoque B (factuel ou statistique). |
| `prevents` | A empêche B. |
| `requires` | B nécessite A pour exister. |
| `enables` | A rend B possible (sans le provoquer). |
| `precedes` | A se produit avant B (temporel seul). |
| `contradicts` | A et B sont logiquement incompatibles. |

**Relations structurelles (support)**

| Relation | Sémantique |
|----------|------------|
| `is_a` | A est un type de B. |
| `part_of` | A est un composant de B. |
| `instance_of` | A est une instance concrète de B. |
| `similar_to` | A ressemble à B sans en être une instance. |
| `property_of` | A est une propriété de B. |

**Fallback**

| Relation | Sémantique |
|----------|------------|
| `related_to` | Lien noté mais non qualifié. Signe d'extraction paresseuse — à minimiser. |

---

## 7. Format d'ingestion (JSON)

```json
{
  "doc_id": "note_cache_redis_2026",
  "nodes": [
    {
      "id": "ttl_trop_court",
      "label": "TTL trop court",
      "type": "claim",
      "description": "Valeur de TTL inférieure au temps moyen entre deux requêtes successives."
    }
  ],
  "rels": [
    {
      "src": "ttl_trop_court",
      "dst": "cache_miss_frequent",
      "type": "causes",
      "confidence": 0.9,
      "evidence": "Un TTL < temps inter-requêtes garantit l'expiration entre deux accès."
    }
  ]
}
```

Règles d'identifiant :
- `id` est **toujours** calculé par `slugify(label)` côté ingest : minuscules, NFKD + ASCII, non-alnum → `_`, max 80 chars. Si Claude propose un `id` différent, l'ingest réécrit vers la forme canonique et logge un warning.
- Ré-ingérer un `id` existant **fusionne** : rafraîchit le label, enrichit la description si vide, conserve le max d'importance, ajoute le doc_id à `sources`.
- À l'insertion d'un nouveau nœud, l'ingest effectue un lookup par sous-chaîne. Les candidats proches sont loggés dans `potential_duplicates.jsonl` — pas de fusion automatique.
- Ré-ingestion d'un doc existant : purge des contributions précédentes (retire le doc_id des sources/evidences, supprime l'arête si sources devient vide), puis insertion normale. Les nœuds ne sont **jamais supprimés**.

---

## 8. Calibration de la confiance

| Confidence | Signification |
|-----------:|---------------|
| `1.0` | Explicitement affirmé dans le texte avec un verbe causal direct. |
| `0.7–0.9` | Inférence raisonnable à partir du texte. |
| `0.4–0.6` | Hypothèse plausible mais non démontrée. |
| `< 0.4` | À éviter — ne pas polluer le graphe. |

---

## 9. Workflows

### 9.1 Ingestion agentique (cible)

```
1. Claude reçoit le document.
2. Il lit le document en entier.
3. Pour chaque concept qu'il s'apprête à créer :
   → appel query_graph(concept) — le graphe répond avec le voisinage existant.
   → si un nœud correspond déjà : réutilisation de l'id exact.
   → si non : création du nouveau nœud.
4. Même logique pour les relations.
5. Émission du JSON final → brain.py ingest.
6. Le document source peut être archivé.
```

La retrieval est pilotée par l'extraction elle-même, pas par un skim préventif. Claude ne voit jamais plus que le voisinage du concept qu'il traite à cet instant.

### 9.2 Interrogation agentique (cible)

```
1. Question utilisateur : « Pourquoi X ? » / « Si Y, quoi ? » / « A mène à B ? »
2. Claude appelle query_graph / get_node / traverse selon le besoin.
3. Il continue à appeler des outils tant qu'il n'a pas assez d'éléments.
4. Réponse construite à partir des evidences collectées,
   avec marquage explicite des chaînes de faible confiance.
```

### 9.3 Règles d'or pour Claude

- Ne jamais mélanger certitudes / inférences / spéculations sans marquage explicite.
- Toute affirmation factuelle pointe vers une `evidence` du graphe.
- Si un type sort de la whitelist, le ramener vers le plus proche autorisé.
- Si une relation n'est pas claire, préférer ne rien ingérer plutôt que de polluer.

---

## 10. Roadmap

**Phase 0 — CLI squelette** ✅ *terminée*
- Kuzu + schéma opérationnel.
- `brain.py` : `init`, `ingest`, `find`, `show`, `causes`, `effects`, `paths`, `query`, `stats`, `audit`, `export`, `import`, `merge`, `context`.
- SKILL.md, tests pytest > 90 % de couverture.

**Phase 1 — Expérimentation corpus** *(en cours)*
- 50 articles Wikipedia AI + 3 GitHub READMEs.
- Cycle A (sans preflight) vs Cycle B (avec preflight heuristique) — mesure de la convergence des ids.
- Objectif : quantifier le gain du contexte injecté et identifier les limites du preflight heuristique.
- Affinage de la whitelist (`algorithm` ajouté suite à 14 rejections observées).

**Phase 2 — Backend API + tool use**
- FastAPI exposant les outils graphe à Claude (streaming).
- Extraction agentique : remplace le preflight heuristique par des tool calls à la demande.
- Interrogation agentique : Claude boucle jusqu'à avoir une réponse complète.
- CLI `brain.py` maintenu pour les opérations bas niveau.

**Phase 3 — React frontend**
- Interface d'ingestion : document → trace des tool calls en direct → résultat ingéré.
- Interface d'interrogation : question → trace de raisonnement (quels nœuds touchés, quels voisinages récupérés) → réponse.
- Explorateur de graphe : visualisation du sous-graphe d'un concept ou d'une question.

**Phase 4 — Maturité**
- Embeddings pour résolution floue d'entités (au-delà du substring match).
- Décroissance temporelle de l'importance.
- Détection automatique de contradictions.
- Export/diff pour versionner l'évolution du graphe.

---

## 11. Décisions tranchées (gel)

- Dédup d'arêtes sur `(src, dst, type)` avec tableaux parallèles `evidences[]` et `sources[]`. `confidence` = max observé.
- Ré-ingestion d'un `doc_id` : purge des contributions précédentes, nœuds jamais supprimés.
- `id = slugify(label)` toujours canonique ; réécriture + warning si Claude propose autre chose.
- Anti-doublons : lookup exact + sous-chaîne, log dans `potential_duplicates.jsonl`, pas de fusion automatique. Fusion explicite par `brain.py merge`.
- Types de nœuds **ouverts** : tout type non vide est accepté. Les types inconnus sont loggés dans `extension_requests.jsonl` pour revue, mais le nœud est ingéré. La cohérence vient du lookup avant création (`query_graph`), pas du whitelist.
- Types de relations **stricts** : whitelist dure, tout type inconnu est rejeté + loggé. Les relations sont le cœur sémantique du graphe, leur vocabulaire ne doit pas dériver.
- La retrieval est **à la demande** (tool calls) et non préventive (pas d'injection de tout le graphe).
- Code, doc technique, README et SKILL.md en **anglais**. Ce VISION.md reste en français.
- Le projet est conçu pour s'installer dans n'importe quel dossier local. Aucun chemin absolu hardcodé.
