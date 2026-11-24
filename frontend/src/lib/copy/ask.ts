import type { Lang } from '$lib/i18n';

// Texts of the answer above the search results and of the notice under the search fields. The
// problems are the outcome and refusal codes of server/src/ask/run.rs and server/src/routes/ask.rs.
type AskCopy = {
  answer: string;
  beta: string;
  written: string;
  preparing: string;
  searching: string;
  notice: string;
  logged: string;
  privacy: string;
  steps: Record<string, string>;
  problems: Record<string, string>;
  broken: string;
  askButton: string;
  askHint: string;
  continueIn: string;
  connect: string;
  copyAddress: string;
  copyQuestion: string;
  copied: string;
};

export const askCopy: Record<Lang, AskCopy> = {
  de: {
    answer: 'Antwort',
    beta: 'Beta',
    written:
      'Von einem Sprachmodell aus den Dokumenten geschrieben. Prüfen Sie die verlinkten Seiten.',
    preparing: 'Wird vorbereitet …',
    searching: 'Sucht im Archiv …',
    notice:
      'Was Sie eingeben, wird für die Antwort 90 Tage protokolliert, ohne IP-Adresse. Bitte keine Namen von Privatpersonen eingeben.',
    logged: '90 Tage protokolliert, ohne IP-Adresse',
    privacy: 'Datenschutz',
    steps: {
      list_communes: 'Gemeinde gesucht',
      get_commune: 'Gemeinde angesehen',
      search_documents: 'Dokumente gefiltert',
      get_document: 'Dokument angesehen',
      search_text: 'Seiten durchsucht',
      read_pages: 'Seiten gelesen'
    },
    problems: {
      too_broad:
        'Die Frage ist zu breit für eine Antwort. Versuchen Sie es enger, etwa mit einer Gemeinde oder einem Jahr.',
      budget:
        'Die Frage ist zu breit für eine Antwort. Versuchen Sie es enger, etwa mit einer Gemeinde oder einem Jahr.',
      no_credit: 'Antworten sind gerade nicht verfügbar; die Suche funktioniert weiter.',
      used_up: 'Die Antworten für heute sind aufgebraucht; die Suche funktioniert weiter.',
      busy: 'Gerade werden viele Fragen beantwortet. Bitte in einer Minute erneut suchen.',
      timeout: 'Die Antwort hat zu lange gedauert.',
      empty: 'Es ist keine Antwort entstanden.',
      failed: 'Es ist keine Antwort entstanden.',
      visitor:
        'Sie haben heute die höchste Zahl an Antworten erhalten; die Suche funktioniert weiter.',
      address:
        'Von diesem Anschluss kamen heute schon sehr viele Fragen; die Suche funktioniert weiter.',
      hour: 'Gerade werden sehr viele Fragen gestellt; die Suche funktioniert weiter.',
      long: 'Für eine Antwort ist die Eingabe zu lang: höchstens 500 Zeichen.'
    },
    broken: 'Die Antwort konnte nicht geladen werden. Bitte die Seite neu laden.',
    askButton: 'Diese Frage beantworten',
    askHint: 'Ein Sprachmodell kann aus den Dokumenten eine Antwort schreiben.',
    continueIn: 'Weiterfragen in Claude oder ChatGPT:',
    connect: 'Tractanda verbinden',
    copyAddress: 'MCP-Adresse kopieren',
    copyQuestion: 'Frage kopieren',
    copied: 'Kopiert'
  },
  fr: {
    answer: 'Réponse',
    beta: 'Bêta',
    written: 'Rédigée par un modèle de langage à partir des documents. Vérifiez les pages liées.',
    preparing: 'Préparation …',
    searching: 'Recherche dans les archives …',
    notice:
      'Ce que vous saisissez est journalisé 90 jours pour la réponse, sans adresse IP. Merci de ne pas indiquer de noms de personnes privées.',
    logged: 'Journalisé 90 jours, sans adresse IP',
    privacy: 'Protection des données',
    steps: {
      list_communes: 'Commune cherchée',
      get_commune: 'Commune consultée',
      search_documents: 'Documents filtrés',
      get_document: 'Document consulté',
      search_text: 'Pages parcourues',
      read_pages: 'Pages lues'
    },
    problems: {
      too_broad:
        'La question est trop large pour une réponse. Essayez de la préciser, par exemple avec une commune ou une année.',
      budget:
        'La question est trop large pour une réponse. Essayez de la préciser, par exemple avec une commune ou une année.',
      no_credit:
        'Les réponses sont momentanément indisponibles ; la recherche continue de fonctionner.',
      used_up: 'Les réponses du jour sont épuisées ; la recherche continue de fonctionner.',
      busy: 'Beaucoup de questions sont en cours. Relancez la recherche dans une minute.',
      timeout: 'La réponse a pris trop de temps.',
      empty: 'Aucune réponse n’a été produite.',
      failed: 'Aucune réponse n’a été produite.',
      visitor:
        'Vous avez reçu le nombre maximal de réponses pour aujourd’hui ; la recherche continue de fonctionner.',
      address:
        'Cette connexion a déjà posé de très nombreuses questions aujourd’hui ; la recherche continue de fonctionner.',
      hour: 'Énormément de questions sont posées en ce moment ; la recherche continue de fonctionner.',
      long: 'Pour une réponse, la saisie est trop longue : 500 caractères au plus.'
    },
    broken: 'La réponse n’a pas pu être chargée. Rechargez la page.',
    askButton: 'Répondre à cette question',
    askHint: 'Un modèle de langage peut rédiger une réponse à partir des documents.',
    continueIn: 'Poursuivre dans Claude ou ChatGPT :',
    connect: 'Connecter Tractanda',
    copyAddress: 'Copier l’adresse MCP',
    copyQuestion: 'Copier la question',
    copied: 'Copié'
  },
  it: {
    answer: 'Risposta',
    beta: 'Beta',
    written:
      'Scritta da un modello linguistico a partire dai documenti. Verificate le pagine collegate.',
    preparing: 'Preparazione …',
    searching: 'Ricerca nell’archivio …',
    notice:
      'Ciò che inserite viene registrato per la risposta per 90 giorni, senza indirizzo IP. Non inserite nomi di persone private.',
    logged: 'Registrato 90 giorni, senza indirizzo IP',
    privacy: 'Protezione dei dati',
    steps: {
      list_communes: 'Comune cercato',
      get_commune: 'Comune consultato',
      search_documents: 'Documenti filtrati',
      get_document: 'Documento consultato',
      search_text: 'Pagine cercate',
      read_pages: 'Pagine lette'
    },
    problems: {
      too_broad:
        'La domanda è troppo ampia per una risposta. Provate a restringerla, per esempio con un comune o un anno.',
      budget:
        'La domanda è troppo ampia per una risposta. Provate a restringerla, per esempio con un comune o un anno.',
      no_credit: 'Le risposte non sono al momento disponibili; la ricerca continua a funzionare.',
      used_up: 'Le risposte di oggi sono esaurite; la ricerca continua a funzionare.',
      busy: 'Molte domande sono in corso. Ripetete la ricerca tra un minuto.',
      timeout: 'La risposta ha richiesto troppo tempo.',
      empty: 'Non è stata prodotta alcuna risposta.',
      failed: 'Non è stata prodotta alcuna risposta.',
      visitor:
        'Avete ricevuto il numero massimo di risposte per oggi; la ricerca continua a funzionare.',
      address:
        'Da questa connessione sono già arrivate moltissime domande oggi; la ricerca continua a funzionare.',
      hour: 'In questo momento vengono poste moltissime domande; la ricerca continua a funzionare.',
      long: 'Per una risposta il testo è troppo lungo: al massimo 500 caratteri.'
    },
    broken: 'Non è stato possibile caricare la risposta. Ricaricate la pagina.',
    askButton: 'Rispondere a questa domanda',
    askHint: 'Un modello linguistico può scrivere una risposta a partire dai documenti.',
    continueIn: 'Continuare in Claude o ChatGPT:',
    connect: 'Collegare Tractanda',
    copyAddress: 'Copiare l’indirizzo MCP',
    copyQuestion: 'Copiare la domanda',
    copied: 'Copiato'
  },
  en: {
    answer: 'Answer',
    beta: 'Beta',
    written: 'Written by a language model from the documents. Check the linked pages.',
    preparing: 'Preparing …',
    searching: 'Searching the archive …',
    notice:
      'What you enter is logged for 90 days for the answer, without your IP address. Please do not enter names of private persons.',
    logged: 'Logged for 90 days, without IP address',
    privacy: 'Privacy',
    steps: {
      list_communes: 'Found the municipality',
      get_commune: 'Looked at the municipality',
      search_documents: 'Filtered the documents',
      get_document: 'Looked at the document',
      search_text: 'Searched the pages',
      read_pages: 'Read pages'
    },
    problems: {
      too_broad:
        'The question is too broad for an answer. Try narrowing it, for example to one municipality or one year.',
      budget:
        'The question is too broad for an answer. Try narrowing it, for example to one municipality or one year.',
      no_credit: 'Answers are not available right now; the search keeps working.',
      used_up: 'Today’s answers are used up; the search keeps working.',
      busy: 'Many questions are being answered right now. Please search again in a minute.',
      timeout: 'The answer took too long.',
      empty: 'No answer came out.',
      failed: 'No answer came out.',
      visitor: 'You have had the most answers allowed today; the search keeps working.',
      address: 'Very many questions came from this connection today; the search keeps working.',
      hour: 'Very many questions are being asked right now; the search keeps working.',
      long: 'The text is too long for an answer: 500 characters at most.'
    },
    broken: 'The answer could not be loaded. Please reload the page.',
    askButton: 'Answer this question',
    askHint: 'A language model can write an answer from the documents.',
    continueIn: 'Continue in Claude or ChatGPT:',
    connect: 'Connect Tractanda',
    copyAddress: 'Copy the MCP address',
    copyQuestion: 'Copy the question',
    copied: 'Copied'
  }
};
