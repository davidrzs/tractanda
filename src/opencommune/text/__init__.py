"""The text of each page: the PDF's own text layer, and OCR for the pages without one.

1. `layer`: the pages of chosen section types and what PyMuPDF reads on each: the PDF's text, its
   number of characters and how much of the page an image covers.
2. `queue`: pages cut into chunks on a shared file system. Any number of workers claim chunks at
   once, on different machines and graphics cards. OCR and the privacy check use it.
3. `ocr`: the pages to read (those in scope without usable text), and one OCR worker. It renders
   the pages of the chunks it claims, sends them in batches to PaddleX's PaddleOCR-VL service, and
   appends each answer to its chunk's result file.
4. `store`: DATA_DIR/text.db, the text of every page from every pass and queue.

`opencommune text` runs them; passes and queues live in DATA_DIR/text. A worker needs the PaddleX
service, with PaddleOCR-VL behind a vLLM server, on a GPU next to it. Page text can name people, so
it stays in DATA_DIR and is never published as it is.
"""
