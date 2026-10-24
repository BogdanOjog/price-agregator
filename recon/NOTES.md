# Recon — notițe

## robots.txt și termeni (T0.2, 2026-10-23)

Descărcate cu `httpx`, User-Agent `pisa-prices/0.1 (uz personal)`, 3 s între request-uri. Copii brute: `recon/out/robots/`, `recon/out/terms/`. Scripturi: `recon/fetch_robots.py`, `recon/find_terms.py`.

Aici doar se raportează. Decizia dacă accesul automat e acceptabil e a utilizatorului (T0.7).

### robots.txt

| Host | Status | Reguli relevante |
| --- | --- | --- |
| prenotalaspesa.coopfirenze.it | 200 | `Disallow: *search?q*`, `/*prefn*`, `/*prefv*`, `/*pmax*`, `/*pmin*`, `/*on/demandware*`, plus cont/coș/checkout. `Allow: /on/demandware.store/*` și `/*on/demandware.static/-/Sites/default/*` (regula `Allow` e mai lungă, deci câștigă pentru aceste căi). Sitemap: `/sitemap_index.xml` |
| www.coopfirenze.it | 200 | `Disallow:` gol → totul permis. Sitemap: `/sitemap.xml` |
| spesaonline.conad.it | **404** | Nu există robots.txt → nicio regulă publicată |
| www.conad.it | 200 | `Allow: /`; `Disallow: /excel-volantini.xlsx` (un fișier Excel cu volantine, interzis explicit) |
| www.lidl.it | 200 | `Disallow: /q/search?id=*`, `/1*` … `/9*` (căi care încep cu cifre), `/cc.js*`, `/cdn/assets/cwv/`. Sitemap: `/static/sitemap.xml` |
| www.eurospin.com | 200 | `Allow: /`; `Disallow: /leaflet/`, `/search-results/`, `/area-riservata/`, `/error/`, `/404/`. Sitemap: `/sitemap-index.xml` |

### Termeni

| Site | Pagină | Ce spune despre accesul automat / reutilizarea conținutului |
| --- | --- | --- |
| prenotalaspesa.coopfirenze.it | [Condizioni generali di vendita](https://prenotalaspesa.coopfirenze.it/condizionigeneralidivendita.html) | Nimic despre acces automat sau extragere de date; sunt doar condiții de vânzare. Confirmă art. 4.2 (prețul și promoția se aplică la data pregătirii/ridicării) și art. 4.3 (preț Socio diferit, posibilă limită de bucăți per card). |
| www.coopfirenze.it | — | Nicio pagină de termeni de utilizare găsită (pagina principală + 1044 URL-uri din sitemap); doar documente de privacy. |
| spesaonline.conad.it | [Condizioni di vendita](https://spesaonline.conad.it/condizioni-di-vendita) | Nimic despre acces automat; sunt doar condiții de vânzare. Confirmă art. 6.3 (greutate variabilă ±20%). |
| www.conad.it | — | Nicio pagină de termeni de utilizare găsită (pagina principală + 3445 URL-uri din sitemap). |
| www.lidl.it | [Informazioni legali](https://www.lidl.it/c/informazioni-legali/s10018358) | Doar datele companiei (Lidl Italia S.r.l.; Lidl Plus gestionat de Lidl Stiftung & Co. KG). Nicio clauză despre utilizarea conținutului. |
| www.eurospin.com | [Termini e condizioni](https://www.eurospin.com/it-it/termini-condizioni/) | **Clauză relevantă:** conținutul site-ului e „riservato” și nu poate fi „copiato, riprodotto, trasferito, caricato, pubblicato o distribuito in qualsiasi modo senza il preventivo consenso scritto”; însă „per utilizzo personale è tuttavia consentito immagazzinarli nel proprio computer o di stamparne estratti”. Framing-ul fără autorizare e interzis. |

### De decis de utilizator la T0.7

- **Eurospin:** proiectul stochează local prețurile pentru uz personal și nu publică nimic. Asta pare să corespundă excepției „utilizzo personale”, dar clauza nu menționează explicit accesul automat. Decizia e a ta.
- **spesaonline.conad.it** nu are robots.txt: nu există nicio regulă publicată, nici permisivă, nici restrictivă.
- **Instrumentul de browser pentru T0.3/T0.5:** skill-ul `/browser-automation` folosește patchright, o variantă de Playwright modificată ca să nu fie detectată ca automatizare. Regula proiectului e „nu ocoli protecțiile anti-bot”. Alternativa e Playwright standard (deja în stack), care se identifică normal ca browser automat.

## Coop.fi (T0.3, în lucru — 2026-10-23)

Doar `httpx`, User-Agent din setări, 3 s între request-uri. Răspunsuri brute: `recon/out/coop/raw/` (prenotalaspesa) și `recon/out/coopfirenze/raw/` (coopfirenze.it), fiecare cu `.meta.json` (URL, status, cookie-uri). Client: `recon/politehttp.py`.

### prenotalaspesa.coopfirenze.it (catalog, Salesforce Commerce Cloud)

- **Punct de ridicare Cascina confirmat** în `/stores`: `data-storeid="iestore34446"`, „CASCINA VIA DEL FOSSO VECCHIO”, `data-storepolygon="1253"`. Niciun punct de ridicare în Comuna Pisa.
- **Selecția magazinului:** `storeLocator.js` e pe o cale interzisă de robots.txt (`/on/demandware.static/Sites-CoopFi-Site/...`), deci nu l-am citit. Cu aprobarea utilizatorului (opțiunea 2), **o singură sesiune Playwright standard, headless** (`recon/browser_coop_select_store.py`, ieșire în `recon/out/coop/browser/`): cookie-uri doar necesare → „Dai solo un'occhiata” (vizitator, fără cont) → „Scegli il ritiro in negozio: Seleziona” → Cascina „Seleziona negozio”. Request observat:
  `GET /on/demandware.store/Sites-CoopFi-Site/it_IT/Stores-SelectStore?actionUrl=<url>&storeId=iestore34446&storePolygon=1253&isNewPickingSystemEnabled=true&isFamilyAndFriendsEnabled=false` cu `X-Requested-With: XMLHttpRequest` → JSON `{action, queryString, locale, redirectUrl}`. Alegerea modului „ritiro” nu face request separat.
- **Test de reproducere `httpx` (fără browser): reușit.** Sesiune nouă: `GET /` (cookie-uri `sid`, `dwsid`, `dwanonymous_*`) → GET-ul de mai sus → `Stores-CheckSelectedStore` = `isStoreSelected: true`, `Stores-CurrentStore` = `data-preferredstoreid="iestore34446"`, „CASCINA VIA DEL FOSSO VECCHIO”. Cookie-uri noi după selecție: `preferredStoreId`, `polygonId`, `deliveryInfo`, `firstAvailableSlot`. **Playwright nu e necesar pentru Coop.**
- **Prețuri fără magazin vs Cascina:** pe cele 12 produse comparate (pasta di semola, pagina 1) prețurile sunt identice între: fără magazin, Cascina prin httpx, Cascina în browser. Colectorul selectează totuși Cascina explicit.
- **Arbore de categorii:** extras din meniul paginii principale → `recon/out/coop/categories.json` (805 categorii, 23 de nivel 1). URL de categorie: `/<nivel1>/<nivel2>/<nivel3>/`. `cgid` = calea cu `/` înlocuit de `-` (văzut în URL-ul de paginare generat de site).
- **Pagina de categorie** (fără magazin ales): 200, HTML randat pe server, 12 produse vizibile din „284 risultati” (pasta di semola). Tile: `div.product[data-pid]` (pid de 13 cifre, ex. `0100635078500`), marcă, nume cu gramaj, preț, €/KG, „Prezzo precedente € 1,44”, „Max 2 pezzi per ordine”.
- **Paginare:** site-ul generează `Search-UpdateGrid?cgid=…&pmin=0%2c01&prefn1=excludeForDelivery&prefv1=false&start=12&sz=12`, cu parametri **interziși** de robots.txt (`pmin`, `prefn`, `prefv`). Testat același URL **fără** cei trei parametri (`cgid`, `start`, `sz`): 200, 12 produse noi, zero suprapuneri cu pagina 1. → paginare permisă; parserul trebuie să filtreze singur produsele cu preț 0 / excluse de la livrare.
- **Sitemap** (`/sitemap_index.xml` → `/sitemap_0.xml`): 1339 URL-uri, doar categorii și conținut, **niciun URL de produs**.
- `/promo-soci` fără magazin ales → **500**; cu Cascina aleasă → 200, listare de produse („Tutte le categorie”). Paginarea generată de site: `Search-UpdateGrid?cgid=root&pmin=…&pmid=ufi-members-iestore34446&prefn1=excludeForPickup&prefv1=false&start=12&sz=12`; fără `pmin`/`prefn1`/`prefv1` rămâne permisă (`pmid` nu e interzis).
- **Pagina de produs** (`/<categorie>/<pid>.html`): JSON-LD `Product` cu `sku` = `mpn` = pid intern, `brand`, `offers.price`. **Niciun EAN** nici în JSON-LD, nici în HTML → la Coop potrivirea mărcilor naționale nu se poate face prin EAN (doar `resolve` pe nume + gramaj / canonical).
- **Inventarul promoțiilor** (`recon/out/coop/promotions_seen.json`, din 5 pagini Cascina, ~70 tile-uri):
  | Formă văzută | Unde | `mechanic` + modificatori |
  | --- | --- | --- |
  | `€ 0,95 € 0,95 € 1,90/KG` | `.cc-prices` | preț obișnuit (`price_observations`) |
  | `Prezzo promo per consegne fino al 04/11 −42% … € 0,84 … Prezzo precedente € 1,44` | `.cc-prices`, `.cc-promotions__wrp` | `PRICE_CUT`, `valid_until` = 04/11 (fără an, fără început), `prior_price` |
  | `−50% … Prezzo in promozione: €1.84 € 1,31 Con Carta Soci` + `−30% … € 1,84 Non Soci` | `.cc-promotions__wrp` | **două** promoții: `PRICE_CUT` (Non Soci) și `PRICE_CUT` + `program = coop_socio` (Con Carta Soci); notă: „€1.84” cu punct zecimal |
  | `Max 2 pezzi per ordine` / `Max 6 …` | `span.cc-maximumNumber` | `max_qty_per_receipt` (pe comanda online); apare și fără promoție |
  | `Biologico`, `Kosher food`, `Senza lattosio` | `.cc-productTile__badges` | flag-uri de produs, nu promoții |
  Nevăzute în aceste pagini: 1+1, 3x2, „il secondo al 50%”, „acquisto minimo”, praguri de coș, sottocosto.
- **Contrazicere cu planul inițial** („Prețurile Socio sunt pe pagina «Offerte per i soci», nu pe tile-urile produselor”): pe prenotalaspesa **prețul Socio apare pe tile** („Con Carta Soci”). Pagina „Offerte per i soci” de pe coopfirenze.it e o selecție separată (acum 2 produse). Conform deciziei Q7, promoțiile de pe tile-urile Cascina sunt `channel = online`, deci nu se aplică la Cisanello.
- **Observație pentru T0.7:** promoțiile online Cascina au „fino al 04/11”, aceeași dată de final ca volantinul Cisanello (22/10–04/11), iar volantinul listează și „CASCINA (PI)” și „PISA - Cisanello”. E posibil ca tile-urile Cascina să reflecte volantinul — o sursă structurată pentru promoțiile din magazin. Nu e verificat; decizia e a utilizatorului.
- **Fixture-uri** (`tests/fixtures/coop_catalog/`, fără `<script>` în afară de JSON-LD): categorie pasta pagina 1, `UpdateGrid` start=12, categorie olio-sughi, promo-soci, pagina de produs, răspunsul `SelectStore`; `tests/fixtures/coop_soci/offerte_per_i_soci.html`; `tests/fixtures/coop_flyer/` (JSON-ul magazinului + textul volantinului).

### www.coopfirenze.it (Cisanello, volantine, Offerte per i soci; robots.txt permite tot)

- **Coop Cisanello:** pagina `/negozi-e-promo/negozi/pisa-cisanello?id=001303`, `data-store-code="001303"`; adresa oficială VIA MANARA VALGIMIGLI 1-3, 56124.
- **Lista de volantine per magazin (JSON):** `GET https://www.coopfirenze.it/.puntovendita.store.json?code=001303&t=<ms>`. URL-ul e construit în `clientlib-site.js` de `storeService.loadStore` + `utils.getPageUrl` (pe site-ul public `isOnDispatcher()` e adevărat → `origin + "/" + ".puntovendita.store.json"`). Răspuns: `flyers[]` cu `title`, `folder`, `pdfName`, `uri` (PDF), `dateOn`/`dateOff`, `flyerValidityStart`/`flyerValidityEnd`, `showFlyer`, `renditions`.
- **Volantinul curent Cisanello:** „Volantino Coop.fi”, `folder = ufi-a`, valabil 2026-10-22 → 2026-11-04 (valabil în ~46 de magazine, inclusiv Cascina și Pisa Cisanello).
- **Formatul volantinului: PDF.** AEM publică însă și textul extras: rendition-ul `cqdam.text.txt` (66 676 caractere, `text/plain`). Textul e în ordinea layout-ului, cu artefacte („nvece di i € 8,49”, „ISPARMI R”, „‐‐6060%%”), fără EAN. **Nu e format structurat** → decizie la T0.7 (dar nu ar cere `pymupdf`).
- **Ofertele Socio apar în volantin** („MAX 2 PEZZI PER CARTA SOCIO”, „RISERVATO AI SOCI”, „ESCLUSIVA SOCI”).
- **Offerte per i soci:** `/negozi-e-promo/offerte-e-volantini/offerte-per-i-soci`, HTML randat pe server (componentă `one-column-text`). Acum: 2 produse, „OFFERTE VALIDE DAL 16 AL 31 OTTOBRE”, valabile în toate supermarketurile Unicoop Firenze; text de forma „€ 3,39 a conf. invece di € 8.49 a conf. RISPARMI € 5,10 a conf. MAX 2 PEZZI PER CARTA SOCIO”. Fără EAN → legare prin `resolve` (nume + gramaj).

## Conad (T0.3 — 2026-10-23)

Doar `httpx`. Răspunsuri brute: `recon/out/conad/raw/` (spesaonline) și `recon/out/conad_it/raw/` (conad.it). ~6 request-uri.

- **spesaonline.conad.it** rulează pe Adobe AEM; frontend în `/etc.clientlibs/conad-ecommerce/clientlibs/clientlib-site…js`. API-uri văzute în JS: `/api/ecommerce/it-it.stores.json` (POST, căutare magazine), `it-it.set-ecaccess.json` (POST, alegerea magazinului), `it-it.timeslots.json`, `it-it.first-timeslot-by-stores.json` etc.
- **Alegerea magazinului e protejată anti-bot.** `setChosenStore` adaugă `protectionToken = await window.gpGetProtectionToken("entryaccess")`. `clientlib-global-protection.js` face: detecție de bot **BotD** (`botd.detect()`), fingerprint, `POST /api/common/protection.json?step=zero` cu `{feBot, fingerprint, action}`, plus **reCAPTCHA Enterprise** (cheia din pagină). Conform regulii proiectului („nu ocoli protecțiile”), **nu am încercat** să obțin sau să reproduc tokenul. Un browser automat ar fi detectat de BotD; a-l face nedetectabil ar fi eludare.
- **Categorie fără magazin ales** (`/c/pasta-all-uovo-secca--1001`): 200, HTML randat pe server, „34 risultati”, prețuri vizibile (ex. „Margherite all'Uovo 250 g Conad … 3,00 € / Kg … 0,75€”), dar `pointOfService = null` → **prețuri generice, nu ale magazinului din Via Emanuele Filiberto**. Fiecare tile are `data-product` cu JSON structurat (`code`, `nome`, `marchio`, `netQuantity`, `netQuantityUm`, `categoria*`, `bassiFissi`, `customPromo…`). Textul paginii menționează „Prodotto con limite massimo di pezzi acquistabili in promozione”.
- Categorii: 148 de link-uri `/c/<slug>--<cod>` în pagina principală (ex. `/c/pasta-di-semola--1004`).
- **conad.it:** `/volantini` (din sitemap) → **404**; paginile de magazin nu sunt în sitemap; conad.it încarcă aceeași `clientlib-global-protection` și reCAPTCHA Enterprise. Volantinul magazinului nu a fost găsit.
- ~~Magazinul nu a putut fi confirmat~~ → **confirmat** (link dat de utilizator): `https://www.conad.it/ricerca-negozi/conad-via-emanuele-filiberto-9-56121-pisa--010529` — „Via Emanuele Filiberto 9, **56121** Pisa (PI)”, Cooperativa Conad Nord Ovest, `anacanId = 010529`, „In questo negozio è disponibile il servizio di spesa online con ritiro in negozio”. Pagina conține JSON-ul volantinelor magazinului (`title`, `pdfUrl`, `validFrom`/`validTo` epoch ms, `hasDisaggregated`, `meccanicaPromozione`): acum 3 volantine, toate `flyerFormat = pdf`, `hasDisaggregated = false`. Nota anterioară: `it-it.stores.json` (căutarea după adresă) nu cere token, dar corpul POST-ului e construit din obiectul Google Maps al adresei, iar formatul exact nu reiese din JS fără să-l observ; nu l-am ghicit.
- **Decizie utilizator (2026-10-23):** pentru Conad se folosește varianta 2. Utilizatorul alege manual, în browserul lui, „Ordina e Ritira” + magazinul 010529 (trecând el protecția), iar colectorul refolosește cookie-ul de sesiune în `httpx`. Cookie-ul stă în `data/conad_session.txt` (ignorat de git) și se reîmprospătează manual când expiră. Colectorul nu generează și nu reproduce niciodată `protectionToken`.

### Conad cu sesiunea utilizatorului (magazin 010529)

- Prima copie cURL nu avea magazin (`referer: /entry`, fără cookie `ecAccess`) → `pointOfService = null`. A doua copie are **`ecAccess`** (JWT HS256; payload: `typeOfService = ORDER_AND_COLLECT`, `pointOfServiceId = 010529`, `cartId`, `anonymousCartId`) și `ecCooperative=cno`.
- Cookie-uri necesare (fără cele de analytics Adobe): `ecSess`, `ecRoute`, `affinity`, `ecServerUUID`, `ecAccess`, `ecCooperative`. Trimise cu User-Agent-ul nostru declarat (nu cel al browserului utilizatorului) → funcționează.
- **Verificare:** pagina de categorie conține `pointOfService = {"address": {"formattedAddress": "VIA EMANUELE FILIBERTO, 9, Toscana, PISA, 56121", …}, "name": "010529", "cartaInsieme": true, …}`.
- **Prețuri magazin vs generice** (pasta all'uovo, 27 de produse comune): **20 diferă**; fără magazin multe produse au `basePrice: 0.0`, iar altele alt preț (ex. Risini all'Uovo 0,75 → 0,69 €). → prețurile generice nu sunt utilizabile; sesiunea e necesară.
- **Date de produs structurate:** fiecare card `div.component-ProductCard` are `data-product` (JSON): `code`, `nome`, `marchio`, `basePrice`, `netQuantity`, `netQuantityUm`, `categoria{Primo,Secondo,Terzo}Livello`, `bassiFissi`, `marketingCategoryBadge`, `customPromoSponsor` (listă de coduri de campanii interne).
- **Paginare:** link-uri `javascript:void(0)` cu `data-page`; JS-ul (`updateColMain`) cere `<.col-main data-search-endpoint> + "?page=N"`. Pentru pasta di semola: `GET /c/level2/_jcr_content/root/search.loader.html/pasta-di-semola--1004.html?page=2` → 200, 30 de produse noi, zero suprapuneri. 30 de produse pe pagină.
- **Pagina de produs** `/p/<slug>--<code>`: JSON-LD `Product` cu **`gtin` (EAN)**, ex. `8003170062443` pentru 401495. → la Conad EAN-ul e disponibil (o cerere în plus per produs).
- **Categorii:** `recon/out/conad/categories.json`, din link-urile `/c/<slug>--<cod>` ale paginii principale; codul ierarhic (2 cifre pe nivel, ex. `10` → `1004`).
- **Inventarul promoțiilor** (`recon/out/conad/promotions_seen.json`, ~150 de carduri din pasta di semola p1–p2, pasta all'uovo, latte):
  | Formă văzută | Unde | `mechanic` + modificatori |
  | --- | --- | --- |
  | `1,75€`, `3,50 € / Kg` | `.product-price`, `data-product.basePrice` | preț obișnuit |
  | logo „Bassi e Fissi” + preț roșu | `img.logo-iniziativa`, `.product-price-red` | **nu e promoție** (preț mic permanent; `bassiFissi = true`) |
  | iconiță fidelitate, `<s>1,75€</s> 1,45€` | `.component-PromoBadgePotentialDiscount` | `PRICE_CUT` + `program = conad_carta_insieme`; **fără date de valabilitate** pe card sau pe pagina produsului |
  | `-20% … Fino al 4 novembre … 0,98€ 0,78€` | `.promo-badges` + prețuri | `PRICE_CUT`, `valid_until` „4 novembre” (fără an, fără început), prețul anterior afișat |
  | filtru „Taglio Netto (31)” | bara de filtre | `UNKNOWN` — nevăzut încă pe un card |
  Textul „Prodotto con limite massimo di pezzi acquistabili in promozione” și „Aggiungi altri N pezzi per applicare l'offerta” apar în șabloanele ascunse ale tuturor cardurilor; nu sunt informații per produs în HTML-ul static.
- **Volantinele magazinului** (din pagina conad.it a magazinului): 3, toate PDF (`pdfUrl`), cu `validFrom`/`validTo`; `hasDisaggregated = false` → nicio variantă structurată. Decizie la T0.7.
- **Fixture-uri:** `tests/fixtures/conad_catalog/` (categorii p1, `search.loader` p2, latte, pasta all'uovo cu și fără magazin, pagina de produs). ID-urile coșului anonim au fost înlocuite cu zerouri. Dump-urile brute (`recon/out/*/raw/`) conțin cookie-urile trimise și sunt ignorate de git.
- **Request-uri Conad în total:** ~13 (spesaonline + conad.it).

## Lidl (T0.5 — 2026-10-23)

Doar `httpx`, ~7 request-uri. Răspunsuri brute: `recon/out/lidl/raw/`. **Test de reproducere `httpx`: reușit** (fără cookie-uri, fără browser).

- **Pagini de oferte** (link-uri de pe pagina principală, căi `/c/…/a<id>`, permise de robots.txt): `/c/lidl-plus-kw-43-26/a10105138` (25 de produse), `/c/super-offerte-kw-43-26/a10105140` (14), `/c/lidl-mega-offerte-kw-43-26/a10105139` (5), toate „Da giovedì 22/10”. Slug-ul și id-ul se schimbă săptămânal (`kw-43-26`) → colectorul le ia din pagina principală, nu le construiește. Valul „de luni” nu apărea acum pe pagina principală.
- **Format:** fiecare produs are `data-grid-data` = JSON complet, randat pe server: `productId`, `erpNumber`, `title`, `brand`, `keyfacts.wonCategoryPrimary`, `storeStartDate`/`storeEndDate` (epoch s), `regions`, `regionsV2`, `regionsPrices`, `canonicalUrl`. Același produs poate apărea pe mai multe pagini → deduplicare după `productId`.
- **Prețuri:** `regionsPrices[<regionPriceId>]` conține
  - `currentPrice` = `{price, endDate, discount: {deletedPrice, discountText}, packaging.text, basePrice.text}` → `PRICE_CUT` (preț vechi = `deletedPrice`); fără `startDate` → se folosește `storeStartDate`;
  - `currentLidlPlusPrice` = `{price: {price, oldPrice, startDate, endDate, discount, packaging, basePrice}, lidlPlusText: "Con Lidl Plus", highlightText}` → `PRICE_CUT` + `program = lidl_plus`. `discountText` poate fi procent (`-26%`) sau sumă (`-0.50€`).
  - Cupoane Lidl Plus de o unitate / „1+1” / praguri: **nevăzute** în paginile publice (cupoanele cer login în aplicație).
- **Ambalaj:** `packaging.text` („1,5 kg rete”, „Sfusa al kg”, „120 g (sgocc.) conf.”, „2x 80 g confezione”) + `basePrice.text` („1 kg = 1.46 €”) → intrare pentru `ingest/units.py`.
- **Regiuni:** `regionsV2` mapează depozite (Arcole, Biandrate, Pontedera - Toscana `600`, …) pe `regionPriceId`. În săptămâna 43 toate produsele aveau un singur `regionPriceId` (`"1"`). **Magazinul din Pisa nu își arată regiunea**; presupunere: `600` „Pontedera - Toscana” (singura toscană). Colectorul: produs disponibil doar dacă `regionsV2` conține `600`; dacă `regionPriceId` diferă între regiuni → `needs_review`. De confirmat la T0.7.
- **Magazin confirmat:** `/s/it-IT/ricerca-negozio/pisa-pi/via-aurelia-nord-4/` — „Negozio Lidl Via Aurelia Nord, 4, Pisa (PI) 56122”, singurul din Pisa. Fără id public pe pagină.
- **EAN:** absent din JSON-ul de pe grilă (`erpNumber`, `productId` sunt interne).
- **Fixture-uri:** `tests/fixtures/lidl_offers/` (cele 3 pagini, fără `<script>`). Inventar: `recon/out/lidl/promotions_seen.json`.

## Lidl prin Everli (2026-10-24)

Lidl Italia nu vinde online alimente; `lidl.it/c/spesa-online` duce la vitrina Everli `lidl.everli.com`. Recon: `recon/everli_public.py` (doar pagini permise), `recon/browser_everli_lidl.py` (o singură vizită cu browserul, ca să se vadă cererile), `recon/everli_httpx.py` (același flux doar cu httpx: reușit, 3 request-uri).

- **robots.txt:** `lidl.everli.com` are `Allow: /`. Site-ul general `it.everli.com` interzice `/supermercato/`, `/s?` și `/referrals/` și nu e folosit.
- **Termeni Everli** (art. 11.2): copierea conținutului e interzisă „salvo che per uso personale e non commerciale”; proiectul e strict personal și nu publică date colectate. Art. 4.3: prețul afișat poate include „eventuala maggiorazione applicata da EVERLI”.
- **Sesiune:** `GET /` → `GET /api/config?brand=50&postal_code=…&address=…&street_number=…&city=…&country=Italia&lat=…&lng=…` (adresa magazinului, publică) pune adresa în sesiune. Răspunsul `config.store.id` e magazinul servit: pentru Via Lorenzo il Magnifico 24, San Giuliano Terme (Ghezzano) e **5390**, chiar magazinul la care merge utilizatorul. Verificare obligatorie la fiecare rulare: alt id → rularea se oprește. Fără adresă: `{"code": "no_address"}`.
- **Categorii:** `GET /api/tags`: 20 de categorii principale, 510 noduri (`id`, `name`, `children`, `product_sample`). Cele alimentare: 3, 1, 4, 6, 8, 12, 32, 27, 28, 10, 11, 15, 101366.
- **Produse:** `GET /api/products?tags=<id>&take=1000&skip=N` → `data[]` + `links.next` (null = ultima pagină). Câmpuri: `id` (unic; `gdo_id` se repetă între produse), `store_id`, `name`, `short_description` („confezione 0.250kg”), `tags` (frunză, mijloc, vârf), `type` (`kilogram`…) + `value` (cantitatea), `price` și `discounted_price` în cenți, `price_per_type`, `variable_weight`, `brand`. Prima colectare: 2.032 de produse alimentare cu 15 request-uri.
- **Adaosul Everli, măsurat:** comparând prețul vechi („prezzo barrato”) al ofertelor lidl.it cu prețul Everli al aceluiași produs, Everli e cu **~8% mai scump** (Patate Selenella 2,99 → 3,25; Mele Golden 1,99 → 2,15; Braciole di suino 4,69 → 5,09; Hamburger di bovino 5,79 → 6,25; Limoni 2,49 → 2,69). Observația păstrează prețul Everli; aplicația afișează și planifică prețul estimat la raft (Everli / 1,08, `price_markup_pct: 8` în `stores.yaml`), cu eticheta „estimat la raft: Lidl Ghezzano (Everli) −8%”. Reducerile Everli (`discounted_price`) sunt `channel = online`.

## Eurospin (T0.5 — 2026-10-23)

Doar `httpx`, ~9 request-uri (inclusiv 3 sitemap-uri). Răspunsuri brute: `recon/out/eurospin/raw/`. **Test de reproducere `httpx`: reușit** (fără cookie-uri). `/leaflet/` nu a fost atins.

- **Listarea** `/it-it/prodotti/?by_typeOffer=offerta&by_typeOffer=family&by_typeOffer=limitati&by_typeOffer=ribassati`: HTML randat pe server, 12 carduri/pagină. Paginarea e publicată ca link: `/it/it/home/prodotti.html?page=N&size=12&by_typeOffer=…` (redirecționează la `/it-it/prodotti/?page=N…`). Pagina 2: 13 carduri, 1 repetat → deduplicare după cod.
- **Card** `li.product-card`: `data-product-code` (ex. `171479`), `data-identifier` (`EUS-COD-10851701-17147901`), **`data-promotion-type`** (`offerta` / `family` / `ribassati` / `limitati`, uneori gol), `.product-card__badge-date-value` = „Fino al **01/11/2026**” (cu an), titlu, marcă, gramaj („800g”), €/kg („8,74€/Kg”), `.product-card__price-label` + prețuri.
- **Ambiguitate de preț (de decis la T0.7):** **toate** tipurile (`offerta`, `ribassati`, `family`) afișează eticheta „Prezzo Family 6,99€ / Prezzo pieno 8,99€”; `limitati` are doar „Prezzo Family”. Pagina produsului (`/it-it/prodotti/<slug>-<cod>/`) arată doar „8,99 € 6,99 €”, fără „Family”. Nu se poate stabili din HTML dacă prețul redus cere contul Eurospin Family. Interpretare propusă (neconfirmată): `family` → `PRICE_CUT` + `program = eurospin_family`; `offerta` / `ribassati` → `PRICE_CUT` pentru toți; `limitati` → `PRICE_CUT` + `is_sottocosto` (stoc limitat). Textul paginii mai spune „Per visualizzare i prezzi di tutti i prodotti accedi o registrati”.
- **EAN:** absent (JSON-LD `Product` are `sku` = `EUS-COD-…`, fără `gtin`).
- **Magazine confirmate** (din `sitemap-stores.xml`, 1262 de magazine): `/it-it/punti-vendita/pisa-via-da-morrona/` — „Pisa Via Da Morrona, 6 56100 Pisa”, `data-store-id = 406298`; `/it-it/punti-vendita/pisa-via-del-chiassatello-28/` — „Pisa Via Del chiassatello, 28 56100 Pisa”, `data-store-id = 480616`. Site-ul folosește **CAP-ul generic 56100**, care nu trece regula 56121–56128; în `stores.yaml` am păstrat 56121 din directoare (de confirmat).
- **Volantine:** ambele magazine au aceleași volantine („settimana-43-2026-ita”, „p25-2026-…”), găzduite pe `www.eurospin.it/smt-digitalflyer/punti-vendita/pisa{,-1}/promozioni/…` — **host neaprobat** (`eurospin.it`), nu l-am deschis.
- **Dependența de magazin a listării:** neverificată. Alegerea magazinului („Imposta come negozio preferito”) pare legată de contul de utilizator („Effettua il login per visualizzare il tuo negozio preferito”); listarea fără magazin e cea națională.
- **Fixture-uri:** `tests/fixtures/eurospin_offers/` (listare p1, p2, pagina magazinului Da Morrona). Inventar: `recon/out/eurospin/promotions_seen.json`.

## Cantități și €/kg pe paginile reale (T1.2 — 2026-10-23)

Verificare cu `ingest/units.parse_quantity` pe toate produsele din fixture-uri:
- Conad 159/159, Lidl 44/44, Eurospin 24/24, Coop 45/48 (nerecunoscute: pește vândut la greutate fără gramaj în nume, două articole de bucătărie).
- **€/kg-ul afișat de site e calculat pe prețul curent, cu reducerea aplicată** (Conad o spune explicit: „I prezzi per unità … sono mostrati con sconto già applicato”). Comparația cu €/kg-ul nostru se face pe același preț.
- Conad scrie uneori unitatea înaintea numărului: „Pappardelle g. 250”.
- **Conserve în lichid:** Coop și Eurospin calculează €/kg pe greutatea scursă, care nu apare în nume (ex. „Piselli bio 300 gr” la 2,43 € → 12,15 €/kg ⇒ 200 g scurse; „Fagioli cannellini 400g” la 0,35 € → 1,46 €/kg ⇒ 240 g). Toate cele 7 nepotriviri rămase sunt de acest tip → `review_items` de tip `unit_price_mismatch`, fără corecție automată.

## Conad: fructe „al kg” (descoperit la prima colectare completă — 2026-10-24)

Pentru produsele vândute la greutate („Pompelmi rossi al kg”), `data-product` spune `netQuantity: 1.0, netQuantityUm: "KG"`, dar prețul afișat (și `basePrice`) e pentru **o bucată estimată**: cardul arată „400 g … 2,08 € / Kg … 0,83€”. Colectorul convertește: produs la greutate, preț pe kg = preț × 1000 / greutatea bucății. Eroarea a fost prinsă de verificarea €/kg (20 de nepotriviri înainte de corecție, 7 după — cazuri reale de verificat).
