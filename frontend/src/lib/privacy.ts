// The privacy page (/datenschutz). It describes what the server and the site actually do:
// server/src/usage.rs for the statistics, server/src/site.rs and the front end for the rest.
import type { Lang } from '$lib/i18n';

type Section = { title: string; paragraphs: string[] };
export const privacy: Record<
  Lang,
  { title: string; updated: string; intro: string; sections: Section[]; contact: string }
> = {
  de: {
    title: 'Datenschutz',
    updated: 'Stand: 24. November 2026',
    intro:
      'Diese Seite erklärt, welche Daten Tractanda beim Besuch der Website, bei der Nutzung der API und des MCP-Servers verarbeitet, und wie mit Personen umgegangen wird, die in den Dokumenten der Gemeinden vorkommen.',
    sections: [
      {
        title: 'Verantwortlich',
        paragraphs: [
          'Tractanda ist ein Projekt von David Zollikofer. Fragen und Anliegen zum Datenschutz nehmen wir per E-Mail entgegen.'
        ]
      },
      {
        title: 'Besuch der Website',
        paragraphs: [
          'Die Website setzt keine Cookies, verwendet keine Analyse- oder Werbedienste und lädt nichts von Dritten nach; auch die Schrift wird von unserem eigenen Server geladen.',
          'Für die Nutzungsstatistik zählt der Server Aufrufe pro Tag, Seitentyp und Antwortstatus, bei automatisierten Abrufen auch den Namen des Programms (zum Beispiel «Googlebot»). Kommt jemand über einen Link auf einer anderen Website, zählt er nur deren Domain (zum Beispiel «google.com»), ebenfalls pro Tag und ohne Bezug zu einer Person. IP-Adressen, vollständige Adressen der aufgerufenen und der verlinkenden Seiten und Suchbegriffe werden für die Statistik nicht gespeichert; was Sie ins Suchfeld eingeben, wird jedoch für die Antwort über den Suchergebnissen 90 Tage protokolliert (siehe unten). Die Zahl der Besuchenden pro Tag wird aus einem Prüfwert von IP-Adresse und Browserkennung geschätzt, der mit einem täglich wechselnden Schlüssel gebildet wird; der Schlüssel wird am Folgetag gelöscht, sodass niemand über Tage hinweg wiedererkannt werden kann. Geschätzt wird zweimal: für alle, die eine Seite abrufen, und für jene, deren Browser die Website auch ausführt; so lassen sich Menschen von Programmen unterscheiden, die nur Seiten abrufen. Wer «Do Not Track» oder «Global Privacy Control» sendet, fliesst weder in diese Schätzungen noch in die Zählung der Domains ein. Die Zählungen pro Tag werden dauerhaft aufbewahrt; sie enthalten keine Angaben zu Personen. Die Prüfwerte eines Tages werden am Folgetag gelöscht, nur ihre Anzahl bleibt.',
          'Die Protokolle des Servers enthalten nur den Seitentyp einer Anfrage, keine Adressen und keine Suchbegriffe.'
        ]
      },
      {
        title: 'API und MCP-Server',
        paragraphs: [
          'Anfragen an die API und an den MCP-Server werden wie Seitenaufrufe nur gezählt, pro Tag und Abfrage; Suchbegriffe werden nicht gespeichert, nur ob eine Suche Treffer hatte.',
          'Wer Tractanda über einen KI-Assistenten wie Claude oder ChatGPT nutzt, gibt seine Fragen dort ein. Für diese Eingaben gilt die Datenschutzerklärung des jeweiligen Anbieters; Tractanda erhält nur die Abfrage, die der Assistent an den MCP-Server schickt.'
        ]
      },
      {
        title: 'Antworten über den Suchergebnissen (Beta)',
        paragraphs: [
          'Wer im Suchfeld sucht oder fragt, erhält über der Trefferliste eine Antwort, die ein Sprachmodell aus den Dokumenten des Archivs schreibt. Dafür schickt unser Server die Eingabe an den Anbieter des Modells, derzeit Anthropic in den USA (Modell Claude Haiku). Der Anbieter erhält die Eingabe und die Ergebnisse der Suche im Archiv, nicht Ihre IP-Adresse. Für die Verarbeitung dort gelten seine Bedingungen.',
          'Wir protokollieren die Eingabe, die Antwort und die Suchschritte 90 Tage lang, um die Antworten zu verbessern, zusammen mit Datum, Uhrzeit, Sprache, Rechenaufwand und Kosten. IP-Adresse, Browserkennung oder ein Wert, mit dem sich zwei Eingaben derselben Person zuordnen liessen, speichern wir nicht. Nach 90 Tagen werden die Einträge gelöscht; dauerhaft bleibt nur die Zahl der Fragen pro Tag. Bitte geben Sie keine Namen von Privatpersonen oder andere persönliche Angaben ein.',
          'Gegen Missbrauch begrenzt der Server die Zahl der Antworten pro Tag. Dazu zählt er sie im Arbeitsspeicher unter einem Prüfwert von IP-Adresse und Browserkennung, gebildet mit einem täglich wechselnden Schlüssel; diese Zählung wird nirgends gespeichert und gilt auch für Browser, die «Do Not Track» senden. Vor jeder Antwort löst Ihr Browser eine kleine Rechenaufgabe, die etwa eine Sekunde dauert; sie hält automatisierte Abfragen fern.'
        ]
      },
      {
        title: 'Personen in den Dokumenten',
        paragraphs: [
          'Das Archiv beruht auf öffentlich zugänglichen Dokumenten der Gemeinden und ihrer Parlamente und verlinkt auf das Original bei der Gemeinde. In den Titeln erscheinen Namen von Privatpersonen als [name].',
          'Für die Volltextsuche ersetzt ein automatisches Verfahren Namen, Strassenadressen, E-Mail-Adressen, Geburtsdaten und Handynummern im Text der Seiten durch Platzhalter, auch dort, wo ein Name im selben Dokument erneut vorkommt. Dabei können einzelne Angaben übersehen werden.',
          'Wer eine Angabe zu einer Person findet, die nicht hierher gehört, kann uns schreiben. Wir prüfen jede Meldung und entfernen die Angabe, wo nötig.'
        ]
      },
      {
        title: 'Ihre Rechte',
        paragraphs: [
          'Sie können nach dem Schweizer Datenschutzgesetz Auskunft über Daten zu Ihrer Person verlangen und deren Berichtigung oder Löschung beantragen. Eine E-Mail genügt.'
        ]
      },
      {
        title: 'Links zu anderen Websites',
        paragraphs: [
          'Links führen zu den Websites der Gemeinden, zu GitHub und zu Hugging Face, auf der Seite «Lizenzen und Dank» auch zu den Projekten, auf denen Tractanda aufbaut. Für diese Websites gelten deren eigene Datenschutzerklärungen.'
        ]
      }
    ],
    contact: 'E-Mail zum Datenschutz schreiben'
  },
  fr: {
    title: 'Protection des données',
    updated: 'État au 24 novembre 2026',
    intro:
      'Cette page explique quelles données Tractanda traite lors de la visite du site, de l’utilisation de l’API et du serveur MCP, et comment sont traitées les personnes mentionnées dans les documents des communes.',
    sections: [
      {
        title: 'Responsable',
        paragraphs: [
          'Tractanda est un projet de David Zollikofer. Les questions relatives à la protection des données peuvent nous être adressées par e-mail.'
        ]
      },
      {
        title: 'Visite du site',
        paragraphs: [
          'Le site n’utilise pas de cookies, aucun service d’analyse ou de publicité, et ne charge rien de tiers ; la police de caractères est elle aussi servie par notre propre serveur.',
          'Pour les statistiques d’utilisation, le serveur compte les accès par jour, par type de page et par statut de réponse, et pour les accès automatisés aussi le nom du programme (par exemple « Googlebot »). Pour une visite arrivant par un lien sur un autre site, il ne compte que le domaine de ce site (par exemple « google.com »), également par jour et sans lien avec une personne. Les adresses IP, les adresses complètes des pages consultées et des pages d’origine ainsi que les termes de recherche ne sont pas enregistrés pour les statistiques ; ce que vous saisissez dans le champ de recherche est toutefois journalisé 90 jours pour la réponse au-dessus des résultats (voir plus bas). Le nombre de visiteurs par jour est estimé à partir d’une empreinte de l’adresse IP et de l’identifiant du navigateur, calculée avec une clé qui change chaque jour ; la clé est effacée le lendemain, de sorte que personne ne peut être reconnu d’un jour à l’autre. Cette estimation est faite deux fois : pour tous ceux qui consultent une page, et pour ceux dont le navigateur exécute aussi le site, ce qui distingue les personnes des programmes qui ne font que télécharger des pages. Les navigateurs qui envoient « Do Not Track » ou « Global Privacy Control » ne sont pris en compte ni dans ces estimations ni dans le décompte des domaines. Les décomptes par jour sont conservés sans limite de durée ; ils ne contiennent aucune donnée personnelle. Les empreintes d’un jour sont effacées le lendemain ; seul leur nombre est conservé.',
          'Les journaux du serveur ne contiennent que le type de page d’une requête, ni adresses ni termes de recherche.'
        ]
      },
      {
        title: 'API et serveur MCP',
        paragraphs: [
          'Comme les pages, les requêtes à l’API et au serveur MCP sont seulement comptées, par jour et par requête ; les termes de recherche ne sont pas enregistrés, seulement le fait qu’une recherche a donné des résultats.',
          'Qui utilise Tractanda à travers un assistant IA comme Claude ou ChatGPT y saisit ses questions. La déclaration de protection des données du fournisseur concerné s’applique à ces saisies ; Tractanda ne reçoit que la requête que l’assistant envoie au serveur MCP.'
        ]
      },
      {
        title: 'Réponses au-dessus des résultats (bêta)',
        paragraphs: [
          'Une recherche ou une question saisie dans le champ de recherche reçoit, au-dessus de la liste des résultats, une réponse rédigée par un modèle de langage à partir des documents des archives. Pour cela, notre serveur envoie la saisie au fournisseur du modèle, actuellement Anthropic aux États-Unis (modèle Claude Haiku). Il reçoit la saisie et les résultats de la recherche dans les archives, mais pas votre adresse IP. Ses propres conditions s’appliquent au traitement chez lui.',
          'Nous journalisons la saisie, la réponse et les étapes de recherche pendant 90 jours afin d’améliorer les réponses, avec la date, l’heure, la langue, le volume de calcul et le coût. Nous n’enregistrons ni l’adresse IP, ni l’identifiant du navigateur, ni aucune valeur permettant de relier deux saisies d’une même personne. Après 90 jours, les entrées sont supprimées ; seul le nombre de questions par jour est conservé durablement. Merci de ne pas indiquer de noms de personnes privées ni d’autres données personnelles.',
          'Pour prévenir les abus, le serveur limite le nombre de réponses par jour. Il les compte en mémoire vive sous une valeur de contrôle dérivée de l’adresse IP et de l’identifiant du navigateur, à l’aide d’une clé qui change chaque jour ; ce décompte n’est enregistré nulle part et s’applique aussi aux navigateurs qui envoient « Do Not Track ». Avant chaque réponse, votre navigateur résout un petit calcul d’environ une seconde, qui tient les requêtes automatisées à distance.'
        ]
      },
      {
        title: 'Personnes mentionnées dans les documents',
        paragraphs: [
          'L’archive repose sur des documents publics des communes et de leurs parlements et renvoie à l’original sur le site de la commune. Dans les titres, les noms de personnes privées apparaissent sous la forme [name].',
          'Pour la recherche en texte intégral, un procédé automatique remplace les noms, les adresses postales, les adresses e-mail, les dates de naissance et les numéros de mobile dans le texte des pages par des marques, y compris là où un nom revient plus loin dans le même document. Certaines indications peuvent lui échapper.',
          'Si vous trouvez une information sur une personne qui n’a pas sa place ici, écrivez-nous. Nous examinons chaque signalement et retirons l’information si nécessaire.'
        ]
      },
      {
        title: 'Vos droits',
        paragraphs: [
          'Selon la loi suisse sur la protection des données, vous pouvez demander des renseignements sur les données vous concernant et en demander la rectification ou l’effacement. Un e-mail suffit.'
        ]
      },
      {
        title: 'Liens vers d’autres sites',
        paragraphs: [
          'Des liens mènent aux sites des communes, à GitHub et à Hugging Face et, sur la page « Licences et remerciements », aux projets sur lesquels Tractanda s’appuie. Leurs propres déclarations de protection des données s’y appliquent.'
        ]
      }
    ],
    contact: 'Écrire un e-mail concernant la protection des données'
  },
  it: {
    title: 'Protezione dei dati',
    updated: 'Stato: 24 novembre 2026',
    intro:
      'Questa pagina spiega quali dati Tractanda tratta durante la visita del sito e l’uso dell’API e del server MCP, e come vengono trattate le persone menzionate nei documenti dei comuni.',
    sections: [
      {
        title: 'Responsabile',
        paragraphs: [
          'Tractanda è un progetto di David Zollikofer. Domande sulla protezione dei dati possono esserci inviate per e-mail.'
        ]
      },
      {
        title: 'Visita del sito',
        paragraphs: [
          'Il sito non usa cookie, nessun servizio di analisi o di pubblicità e non carica nulla da terzi; anche il carattere tipografico viene servito dal nostro server.',
          'Per le statistiche d’uso il server conta gli accessi per giorno, per tipo di pagina e per stato della risposta e, per gli accessi automatizzati, anche il nome del programma (per esempio «Googlebot»). Per una visita che arriva da un link su un altro sito conta solo il dominio di quel sito (per esempio «google.com»), anch’esso per giorno e senza legame con una persona. Indirizzi IP, indirizzi completi delle pagine visitate e di quelle di provenienza e termini di ricerca non vengono registrati per le statistiche; ciò che inserite nel campo di ricerca viene però registrato per 90 giorni per la risposta sopra i risultati (vedi sotto). Il numero di visitatori al giorno è stimato da un’impronta dell’indirizzo IP e dell’identificativo del browser, calcolata con una chiave che cambia ogni giorno; la chiave viene cancellata il giorno seguente, così nessuno può essere riconosciuto da un giorno all’altro. La stima è fatta due volte: per tutti quelli che aprono una pagina e per quelli il cui browser esegue anche il sito; così si distinguono le persone dai programmi che si limitano a scaricare pagine. Chi invia «Do Not Track» o «Global Privacy Control» non viene considerato né in queste stime né nel conteggio dei domini. I conteggi giornalieri vengono conservati senza limiti di tempo e non contengono dati personali. Le impronte di un giorno vengono cancellate il giorno seguente; ne resta solo il numero.',
          'I log del server contengono solo il tipo di pagina di una richiesta, né indirizzi né termini di ricerca.'
        ]
      },
      {
        title: 'API e server MCP',
        paragraphs: [
          'Come le pagine, le richieste all’API e al server MCP vengono solo contate, per giorno e per richiesta; i termini di ricerca non vengono registrati, solo se una ricerca ha dato risultati.',
          'Chi usa Tractanda tramite un assistente IA come Claude o ChatGPT inserisce lì le sue domande. A questi dati si applica l’informativa sulla privacy del rispettivo fornitore; Tractanda riceve solo la richiesta che l’assistente invia al server MCP.'
        ]
      },
      {
        title: 'Risposte sopra i risultati (beta)',
        paragraphs: [
          'Chi cerca o pone una domanda nel campo di ricerca riceve, sopra l’elenco dei risultati, una risposta scritta da un modello linguistico a partire dai documenti dell’archivio. A tal fine il nostro server invia il testo inserito al fornitore del modello, attualmente Anthropic negli Stati Uniti (modello Claude Haiku). Il fornitore riceve il testo e i risultati della ricerca nell’archivio, non il vostro indirizzo IP. Per il trattamento presso di lui valgono le sue condizioni.',
          'Registriamo il testo inserito, la risposta e i passi della ricerca per 90 giorni, per migliorare le risposte, insieme a data, ora, lingua, volume di calcolo e costo. Non salviamo l’indirizzo IP, l’identificativo del browser né alcun valore che permetta di collegare due inserimenti della stessa persona. Dopo 90 giorni le voci vengono cancellate; resta in modo durevole solo il numero di domande al giorno. Vi preghiamo di non inserire nomi di persone private né altri dati personali.',
          'Contro gli abusi il server limita il numero di risposte al giorno. Le conta nella memoria di lavoro sotto un valore di controllo ricavato dall’indirizzo IP e dall’identificativo del browser, con una chiave che cambia ogni giorno; questo conteggio non viene salvato da nessuna parte e vale anche per i browser che inviano «Do Not Track». Prima di ogni risposta il vostro browser risolve un piccolo calcolo di circa un secondo, che tiene lontane le richieste automatizzate.'
        ]
      },
      {
        title: 'Persone menzionate nei documenti',
        paragraphs: [
          'L’archivio si basa su documenti pubblici dei comuni e dei loro parlamenti e rimanda all’originale sul sito del comune. Nei titoli i nomi di persone private appaiono come [name].',
          'Per la ricerca a testo integrale un procedimento automatico sostituisce nel testo delle pagine nomi, indirizzi stradali, indirizzi e-mail, date di nascita e numeri di cellulare con dei segnaposto, anche dove un nome ricompare più avanti nello stesso documento. Alcune indicazioni possono sfuggirgli.',
          'Se trovate un’informazione su una persona che non dovrebbe trovarsi qui, scriveteci. Esaminiamo ogni segnalazione e rimuoviamo l’informazione se necessario.'
        ]
      },
      {
        title: 'I vostri diritti',
        paragraphs: [
          'Secondo la legge svizzera sulla protezione dei dati potete chiedere informazioni sui dati che vi riguardano e chiederne la rettifica o la cancellazione. Basta un’e-mail.'
        ]
      },
      {
        title: 'Link ad altri siti',
        paragraphs: [
          'I link portano ai siti dei comuni, a GitHub e a Hugging Face e, nella pagina «Licenze e ringraziamenti», ai progetti su cui si basa Tractanda. Per questi siti valgono le loro informative sulla privacy.'
        ]
      }
    ],
    contact: 'Scrivere un’e-mail sulla protezione dei dati'
  },
  en: {
    title: 'Privacy',
    updated: 'As of 24 November 2026',
    intro:
      'This page explains which data Tractanda processes when the website is visited and when the API and the MCP server are used, and how people who appear in the documents of the municipalities are treated.',
    sections: [
      {
        title: 'Responsible party',
        paragraphs: [
          'Tractanda is a project by David Zollikofer. Questions and concerns about data protection can be sent to us by e-mail.'
        ]
      },
      {
        title: 'Visiting the website',
        paragraphs: [
          'The website sets no cookies, uses no analytics or advertising services and loads nothing from third parties; the font, too, is loaded from our own server.',
          'For the usage statistics, the server counts requests per day, page type and response status, and for automated requests also the name of the program (for example “Googlebot”). If someone arrives through a link on another website, it counts only that website’s domain (for example “google.com”), likewise per day and without reference to any person. IP addresses, full addresses of the requested and of the linking pages, and search terms are not stored for the statistics; what you type into the search field is, however, logged for 90 days for the answer above the search results (see below). The number of visitors per day is estimated from a hash of the IP address and the browser identifier, computed with a key that changes daily; the key is deleted the following day, so that no one can be recognised from one day to the next. The estimate is made twice: for everyone who requests a page, and for those whose browser also runs the website; this distinguishes people from programs that only fetch pages. Anyone who sends “Do Not Track” or “Global Privacy Control” is included neither in these estimates nor in the count of domains. The counts per day are kept permanently; they contain no information about persons. A day’s hashes are deleted the following day; only their number remains.',
          'The server logs contain only the page type of a request, no addresses and no search terms.'
        ]
      },
      {
        title: 'API and MCP server',
        paragraphs: [
          'Like page requests, requests to the API and to the MCP server are only counted, per day and query; search terms are not stored, only whether a search returned results.',
          'Anyone who uses Tractanda through an AI assistant such as Claude or ChatGPT enters their questions there. These entries are subject to the privacy policy of the provider concerned; Tractanda receives only the query that the assistant sends to the MCP server.'
        ]
      },
      {
        title: 'Answers above the search results (beta)',
        paragraphs: [
          'A search or question typed into the search field gets, above the list of results, an answer that a language model writes from the archive’s documents. For this our server sends what you typed to the model’s provider, at present Anthropic in the USA (model Claude Haiku). The provider receives your input and the results of the searches in the archive, not your IP address. Its own terms apply to the processing there.',
          'We log your input, the answer and the search steps for 90 days to improve the answers, together with the date, time, language, computing effort and cost. We do not store your IP address, your browser’s identification or any value that could link two inputs of the same person. After 90 days the entries are deleted; only the number of questions per day is kept for good. Please do not enter names of private persons or other personal details.',
          'Against abuse, the server limits the number of answers per day. It counts them in memory under a check value derived from the IP address and the browser’s identification with a key that changes every day; this count is not stored anywhere and also applies to browsers that send “Do Not Track”. Before each answer your browser solves a small computation of about a second, which keeps automated requests out.'
        ]
      },
      {
        title: 'People in the documents',
        paragraphs: [
          'The archive is based on publicly accessible documents of the municipalities and their parliaments and links to the original on the municipality’s website. In the titles, names of private individuals appear as [name].',
          'For the full-text search, an automatic procedure replaces names, street addresses, e-mail addresses, dates of birth and mobile phone numbers in the text of the pages with placeholders, including where a name occurs again in the same document. Individual details may be missed.',
          'Anyone who finds information about a person that does not belong here can write to us. We review every report and remove the information where necessary.'
        ]
      },
      {
        title: 'Your rights',
        paragraphs: [
          'Under the Swiss Data Protection Act, you can request information about data relating to you and ask for it to be corrected or deleted. An e-mail is enough.'
        ]
      },
      {
        title: 'Links to other websites',
        paragraphs: [
          'Links lead to the websites of the municipalities, to GitHub and to Hugging Face, and on the “Licences and thanks” page also to the projects on which Tractanda builds. These websites are subject to their own privacy policies.'
        ]
      }
    ],
    contact: 'Send an e-mail about data protection'
  }
};
