# Write a short review of a book from my notes

> Turns the reader's notes on one book into a short review, in their own words.

<!-- Served as the MCP prompt book_review, by the tool get_playbook and, with --resources, as a resource: the same text. -->

1. Ask which book, or call `list_books` and offer the ones with the most notes.
2. Read the notes with `get_notes`.
3. Write three short paragraphs: what the book is about, what stayed with the reader, who should read it. Quote the notes;
   do not add opinions they do not hold.
4. Ask whether to keep the review. If yes, say what you will write and save it with `add_note`.
