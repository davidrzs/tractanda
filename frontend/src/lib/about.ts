export const about = {
  de: {
    title: 'Über Tractanda',
    intro: 'Eine Datenbasis, um die Demokratie in Schweizer Gemeinden besser zu untersuchen.',
    purposeTitle: 'Warum dieses Archiv?',
    purpose:
      'Gemeinden entscheiden über Schulen, Ortsplanung, Infrastruktur und vieles mehr. Die Unterlagen dazu liegen verstreut auf einzelnen Gemeindewebsites. Wer demokratische Prozesse über mehrere Gemeinden oder Jahre hinweg untersuchen und vergleichen möchte, muss sie bisher aufwendig zusammentragen.',
    aim: 'Tractanda sammelt diese Dokumente und erschliesst ihre Inhalte. Damit wollen wir eine gemeinsame Datenbasis schaffen, die Forschung, journalistische Arbeit und den öffentlichen Einblick in kommunale Entscheidungen erleichtert.',
    sourcesTitle: 'Woher kommen die Daten?',
    sources:
      'Die Dokumente stammen aus öffentlich zugänglichen Veröffentlichungen der Gemeinden und ihrer Parlamente. Die jeweiligen Quellen sind bei den Dokumenten verlinkt.',
    methodTitle: 'Was ist bereits möglich?',
    method:
      'Das Archiv verzeichnet die Dokumente der Gemeinden mit einem Link auf das Original. Ein Sprachmodell ordnet jedes Dokument ein: welche Art Dokument es ist, aus welchen Teilen es besteht und von welchem Gremium es stammt. Diese Einordnung ist nicht einzeln geprüft. Sie dient als Einstieg in die Originalquellen, nicht als abschliessend geprüfter Forschungsdatensatz.',
    classificationTitle: 'Wie die Dokumente eingeordnet werden',
    classification:
      'Ein Sprachmodell sieht die Seiten jedes Dokuments als Bilder. Es bestimmt die Dokumentart, die Teile mit ihren Seiten, das Gremium und das Jahr. Niemand prüft diese Angaben einzeln. Jeder Lauf hat einen festen Prompt und feste Einstellungen; was sie für die Daten bedeuten, steht beim Lauf.',
    model: 'Modell',
    limits:
      'Tractanda ist im Aufbau. Eine Gemeinde zählt zum Archiv, sobald mindestens ein Dokument vorliegt. Umfang und Zeiträume unterscheiden sich entsprechend. Fehlende Dokumente bedeuten nicht, dass es keine entsprechenden Sitzungen oder Entscheidungen gab. Vergleiche müssen diese Lücken berücksichtigen.',
    teamTitle: 'Wer steht dahinter?',
    team: 'Tractanda ist ein Projekt von David Zollikofer.',
    contact:
      'Fragen, Hinweise auf fehlende Quellen, Korrekturen oder Anliegen zu Personendaten nehmen wir per E-Mail entgegen. Wir freuen uns auch über den Austausch mit Forschenden, Medienschaffenden und Gemeinden.',
    email: 'David eine E-Mail schreiben',
    emailSubject: 'Kontakt',
    sourceLink: 'Zum offenen Quellcode',
    dataTitle: 'Daten nutzen',
    data: 'Die Daten stehen unter CC0 1.0 und dürfen ohne Bedingungen genutzt werden. Eine Quellenangabe ist rechtlich nicht nötig, aber sehr willkommen. Der Quellcode ist unter der MIT-Lizenz offen.',
    api: 'JSON-API, ohne Schlüssel',
    mcp: 'MCP-Server für KI-Assistenten wie Claude oder ChatGPT',
    dataset: 'Datensatz auf Hugging Face',
    paper: 'Arbeitspapier, ein erster Entwurf (PDF)',
    llms: 'Kurzbeschreibung für KI-Agenten (llms.txt)',
    versionsTitle: 'Versionen',
    versions:
      'Die Daten wachsen mit jeder Version. Eine Version ist ein fester Stand, wie ihn die Website zeigt; wer die Daten zitiert, nennt die Version. Die Fläche zeigt alle Dateien, die der Crawler bisher heruntergeladen hat, auch solche, die noch nicht eingeordnet und deshalb noch nicht im Archiv sind.',
    current: 'Aktuelle Version',
    downloaded: 'Heruntergeladene Dateien',
    listed: 'Dokumente im Archiv',
    counts: {
      documents: 'Dokumente',
      pages: 'Seiten',
      communes: 'Gemeinden',
      searchable_pages: 'im Volltext durchsuchbare Seiten',
      ocr_pages: 'Seiten aus Texterkennung (OCR)'
    },
    first: 'Erste erfasste Version.',
    unchanged: 'Die Zahlen sind unverändert.',
    chartLabel: 'Heruntergeladene Dateien und Dokumente im Archiv, {from} bis {to}',
    privacyTitle: 'Datenschutz',
    privacy:
      'Die Website setzt keine Cookies und lädt nichts von Dritten nach. Für die Nutzungsstatistik zählt der Server Aufrufe pro Tag und Seitentyp, bei Links von anderen Websites nur deren Domain; IP-Adressen, Suchbegriffe und vollständige Adressen der aufgerufenen Seiten speichert er nicht. Was ins Suchfeld eingegeben wird, protokolliert er für die Antwort über den Suchergebnissen 90 Tage, ohne IP-Adresse. Die Zahl der Besuchenden pro Tag wird mit einem täglich wechselnden Schlüssel geschätzt, sodass niemand über Tage hinweg wiedererkannt werden kann. Wer «Do Not Track» oder «Global Privacy Control» sendet, fliesst weder in diese Schätzung noch in die Zählung der Domains ein. Die Zählungen pro Tag werden dauerhaft aufbewahrt; sie enthalten keine Angaben zu Personen.'
  },
  fr: {
    title: 'À propos de Tractanda',
    intro: 'Une base de données pour mieux étudier la démocratie dans les communes suisses.',
    purposeTitle: 'Pourquoi ces archives ?',
    purpose:
      'Les communes prennent des décisions sur les écoles, l’aménagement du territoire, les infrastructures et bien d’autres sujets. Les documents correspondants sont dispersés sur leurs sites web. Étudier et comparer les processus démocratiques entre communes ou au fil des années exige donc un important travail de collecte.',
    aim: 'Tractanda rassemble ces documents et en rend le contenu accessible. Nous souhaitons créer une base de données commune qui facilite la recherche, le travail journalistique et l’accès du public aux décisions communales.',
    sourcesTitle: 'D’où proviennent les données ?',
    sources:
      'Les documents proviennent des publications accessibles au public des communes et de leurs parlements. Les sources sont indiquées par des liens associés aux documents.',
    methodTitle: 'Que peut-on déjà consulter ?',
    method:
      'Les archives recensent les documents des communes, avec un lien vers l’original. Un modèle de langage classe chaque document : de quel type de document il s’agit, de quelles parties il se compose et de quel organe il émane. Ce classement n’est pas vérifié individuellement. Il permet de retrouver les sources originales ; il ne constitue pas un jeu de données de recherche entièrement validé.',
    classificationTitle: 'Comment les documents sont classés',
    classification:
      'Un modèle de langage voit les pages de chaque document sous forme d’images. Il détermine le type de document, les parties avec leurs pages, l’organe et l’année. Personne ne vérifie ces indications une à une. Chaque exécution utilise un prompt et des réglages fixes ; ce qu’ils impliquent pour les données est indiqué pour chaque exécution.',
    model: 'Modèle',
    limits:
      'Tractanda est en cours de développement. Une commune est comptée dans les archives dès qu’au moins un document est disponible. Le nombre de documents et les périodes couvertes varient donc. L’absence de documents ne signifie pas qu’aucune séance ou décision n’a eu lieu. Toute comparaison doit tenir compte de ces lacunes.',
    teamTitle: 'Qui porte le projet ?',
    team: 'Tractanda est un projet de David Zollikofer.',
    contact:
      'Vous pouvez nous écrire pour poser une question, signaler une source manquante, demander une correction ou nous faire part d’un problème concernant des données personnelles. Nous souhaitons aussi échanger avec les milieux de la recherche, les journalistes et les communes.',
    email: 'Écrire un e-mail à David',
    emailSubject: 'Contact',
    sourceLink: 'Voir le code source ouvert',
    dataTitle: 'Utiliser les données',
    data: 'Les données sont publiées sous CC0 1.0 et peuvent être utilisées sans condition. Citer la source n’est pas obligatoire, mais très apprécié. Le code source est ouvert sous licence MIT.',
    api: 'API JSON, sans clé',
    mcp: 'Serveur MCP pour les assistants IA comme Claude ou ChatGPT',
    dataset: 'Jeu de données sur Hugging Face',
    paper: 'Document de travail, premier projet (PDF)',
    llms: 'Description courte pour les agents IA (llms.txt)',
    versionsTitle: 'Versions',
    versions:
      'Les données s’enrichissent à chaque version. Une version est un état fixe, tel que le site le présente ; pour citer les données, indiquez la version. La surface montre tous les fichiers téléchargés jusqu’ici par le robot, y compris ceux qui ne sont pas encore classés et ne figurent donc pas encore dans les archives.',
    current: 'Version actuelle',
    downloaded: 'Fichiers téléchargés',
    listed: 'Documents dans les archives',
    counts: {
      documents: 'documents',
      pages: 'pages',
      communes: 'communes',
      searchable_pages: 'pages consultables en texte intégral',
      ocr_pages: 'pages issues de la reconnaissance de texte (OCR)'
    },
    first: 'Première version enregistrée.',
    unchanged: 'Les chiffres sont inchangés.',
    chartLabel: 'Fichiers téléchargés et documents dans les archives, du {from} au {to}',
    privacyTitle: 'Protection des données',
    privacy:
      'Le site n’utilise pas de cookies et ne charge rien de tiers. Pour les statistiques d’utilisation, le serveur compte les accès par jour et par type de page, et pour les liens venant d’autres sites seulement leur domaine ; il n’enregistre ni adresses IP, ni termes de recherche, ni adresses complètes des pages consultées. Ce qui est saisi dans le champ de recherche est journalisé 90 jours pour la réponse au-dessus des résultats, sans adresse IP. Le nombre de visiteurs par jour est estimé à l’aide d’une clé qui change chaque jour, de sorte que personne ne peut être reconnu d’un jour à l’autre. Les navigateurs qui envoient « Do Not Track » ou « Global Privacy Control » ne sont pris en compte ni dans cette estimation ni dans le décompte des domaines. Les décomptes par jour sont conservés sans limite de durée ; ils ne contiennent aucune donnée personnelle.'
  },
  it: {
    title: 'Informazioni su Tractanda',
    intro: 'Una base di dati per studiare meglio la democrazia nei comuni svizzeri.',
    purposeTitle: 'Perché questo archivio?',
    purpose:
      'I comuni prendono decisioni su scuole, pianificazione del territorio, infrastrutture e molti altri temi. I relativi documenti sono sparsi sui singoli siti comunali. Studiare e confrontare i processi democratici tra comuni o nel corso degli anni richiede quindi un notevole lavoro di raccolta.',
    aim: 'Tractanda raccoglie questi documenti e ne rende accessibili i contenuti. Vogliamo creare una base di dati comune che faciliti la ricerca, il lavoro giornalistico e l’accesso pubblico alle decisioni comunali.',
    sourcesTitle: 'Da dove provengono i dati?',
    sources:
      'I documenti provengono dalle pubblicazioni accessibili al pubblico dei comuni e dei loro parlamenti. Le fonti sono indicate nei collegamenti associati ai documenti.',
    methodTitle: 'Che cosa è già disponibile?',
    method:
      'L’archivio elenca i documenti dei comuni, con un collegamento all’originale. Un modello linguistico classifica ogni documento: di che tipo di documento si tratta, di quali parti è composto e da quale organo proviene. Questa classificazione non è verificata singolarmente. Serve a orientarsi tra le fonti originali; non costituisce un insieme di dati di ricerca interamente validato.',
    classificationTitle: 'Come vengono classificati i documenti',
    classification:
      'Un modello linguistico vede le pagine di ogni documento come immagini. Determina il tipo di documento, le parti con le loro pagine, l’organo e l’anno. Nessuno verifica queste indicazioni una per una. Ogni esecuzione usa un prompt e impostazioni fissi; che cosa significano per i dati è indicato per ogni esecuzione.',
    model: 'Modello',
    limits:
      'Tractanda è in fase di sviluppo. Un comune viene conteggiato nell’archivio non appena è disponibile almeno un documento. La quantità di documenti e i periodi coperti variano quindi. L’assenza di documenti non significa che non si siano tenute sedute o prese decisioni. I confronti devono tenere conto di queste lacune.',
    teamTitle: 'Chi c’è dietro il progetto?',
    team: 'Tractanda è un progetto di David Zollikofer.',
    contact:
      'Potete scriverci per domande, segnalazioni di fonti mancanti, correzioni o questioni relative ai dati personali. Ci interessa anche il confronto con ricercatori, giornalisti e comuni.',
    email: 'Scrivi un’e-mail a David',
    emailSubject: 'Contatto',
    sourceLink: 'Consulta il codice sorgente aperto',
    dataTitle: 'Usare i dati',
    data: 'I dati sono pubblicati con licenza CC0 1.0 e possono essere usati senza condizioni. Citare la fonte non è obbligatorio, ma è molto gradito. Il codice sorgente è aperto con licenza MIT.',
    api: 'API JSON, senza chiave',
    mcp: 'Server MCP per assistenti IA come Claude o ChatGPT',
    dataset: 'Dataset su Hugging Face',
    paper: 'Documento di lavoro, prima bozza (PDF)',
    llms: 'Descrizione breve per agenti IA (llms.txt)',
    versionsTitle: 'Versioni',
    versions:
      'I dati crescono a ogni versione. Una versione è uno stato fisso, come lo mostra il sito; chi cita i dati indica la versione. L’area mostra tutti i file scaricati finora dal crawler, anche quelli non ancora classificati e quindi non ancora presenti nell’archivio.',
    current: 'Versione attuale',
    downloaded: 'File scaricati',
    listed: 'Documenti nell’archivio',
    counts: {
      documents: 'documenti',
      pages: 'pagine',
      communes: 'comuni',
      searchable_pages: 'pagine consultabili nel testo integrale',
      ocr_pages: 'pagine dal riconoscimento del testo (OCR)'
    },
    first: 'Prima versione registrata.',
    unchanged: 'I conteggi sono invariati.',
    chartLabel: 'File scaricati e documenti nell’archivio, dal {from} al {to}',
    privacyTitle: 'Protezione dei dati',
    privacy:
      'Il sito non usa cookie e non carica nulla da terzi. Per le statistiche d’uso il server conta gli accessi per giorno e per tipo di pagina e, per i link da altri siti, solo il loro dominio; non registra né indirizzi IP, né termini di ricerca, né gli indirizzi completi delle pagine visitate. Ciò che viene inserito nel campo di ricerca è registrato per 90 giorni per la risposta sopra i risultati, senza indirizzo IP. Il numero di visitatori al giorno è stimato con una chiave che cambia ogni giorno, così nessuno può essere riconosciuto da un giorno all’altro. Chi invia «Do Not Track» o «Global Privacy Control» non viene considerato né in questa stima né nel conteggio dei domini. I conteggi giornalieri vengono conservati senza limiti di tempo e non contengono dati personali.'
  },
  en: {
    title: 'About Tractanda',
    intro: 'A data foundation for studying democracy in Swiss municipalities more closely.',
    purposeTitle: 'Why this archive?',
    purpose:
      'Municipalities decide on schools, local planning, infrastructure and much more. The documents on these decisions are scattered across individual municipal websites. Anyone who wants to study and compare democratic processes across several municipalities or years has so far had to gather them with considerable effort.',
    aim: 'Tractanda collects these documents and makes their contents accessible. With it we want to create a shared data foundation that makes research, journalistic work and public insight into municipal decisions easier.',
    sourcesTitle: 'Where does the data come from?',
    sources:
      'The documents come from publicly accessible publications of the municipalities and their parliaments. Each document links to its source.',
    methodTitle: 'What is already possible?',
    method:
      'The archive lists the documents of the municipalities with a link to the original. A language model classifies each document: what kind of document it is, which parts it consists of and which body it comes from. This classification is not checked item by item. It serves as a way into the original sources, not as a fully verified research dataset.',
    classificationTitle: 'How the documents are classified',
    classification:
      'A language model sees the pages of each document as images. It determines the document type, the parts with their pages, the body and the year. No one checks this information item by item. Each run has a fixed prompt and fixed settings; what they mean for the data is stated with the run.',
    model: 'Model',
    limits:
      'Tractanda is a work in progress. A municipality counts as part of the archive as soon as at least one document is available. Coverage and time periods vary accordingly. Missing documents do not mean that there were no corresponding meetings or decisions. Comparisons must take these gaps into account.',
    teamTitle: 'Who is behind it?',
    team: 'Tractanda is a project by David Zollikofer.',
    contact:
      'Questions, pointers to missing sources, corrections or concerns about personal data can be sent to us by e-mail. We also welcome exchange with researchers, journalists and municipalities.',
    email: 'Send David an e-mail',
    emailSubject: 'Contact',
    sourceLink: 'See the open source code',
    dataTitle: 'Using the data',
    data: 'The data is published under CC0 1.0 and may be used without conditions. Citing the source is not legally required, but very welcome. The source code is open under the MIT licence.',
    api: 'JSON API, no key needed',
    mcp: 'MCP server for AI assistants such as Claude or ChatGPT',
    dataset: 'Dataset on Hugging Face',
    paper: 'Working paper, a first draft (PDF)',
    llms: 'Short description for AI agents (llms.txt)',
    versionsTitle: 'Versions',
    versions:
      'The data grows with each version. A version is a fixed state, as the website shows it; when citing the data, name the version. The shaded area shows every file the crawler has downloaded so far, including those not yet classified and therefore not yet in the archive.',
    current: 'Current version',
    downloaded: 'Files downloaded',
    listed: 'Documents in the archive',
    counts: {
      documents: 'documents',
      pages: 'pages',
      communes: 'municipalities',
      searchable_pages: 'pages searchable in full text',
      ocr_pages: 'pages from text recognition (OCR)'
    },
    first: 'First recorded version.',
    unchanged: 'The counts are unchanged.',
    chartLabel: 'Files downloaded and documents in the archive, {from} to {to}',
    privacyTitle: 'Privacy',
    privacy:
      'The website sets no cookies and loads nothing from third parties. For the usage statistics, the server counts requests per day and page type, and for links from other websites only their domain; it does not store IP addresses, search terms or the full addresses of the pages requested. What is typed into the search field is logged for 90 days for the answer above the search results, without the IP address. The number of visitors per day is estimated with a key that changes daily, so that no one can be recognised from one day to the next. Anyone who sends “Do Not Track” or “Global Privacy Control” is included neither in this estimate nor in the count of domains. The counts per day are kept permanently; they contain no information about persons.'
  }
};
