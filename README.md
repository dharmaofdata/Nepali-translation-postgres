# PostgreSQL Nepali Translation

Nepali (`ne`) translation of PostgreSQL message catalogs.

## Translation Rules

- Edit only `msgstr`.
- Do not change `msgid`.
- Keep placeholders such as `%s`, `%d`, `%m`, and `%u` exactly correct.
- Preserve escape sequences such as `\n` and `\t`.
- Keep SQL keywords, command names, data types, option names, and file names in English.
- If a translation is uncertain, add a translator comment instead of guessing.
- Do not try to translate everything at once. Make small, reviewed changes.
- Check the PO file with `msgfmt` before committing.

Example:

```po
# Translator: "relation" refers to a PostgreSQL database object.
msgid "relation \"%s\" does not exist"
msgstr "relation \"%s\" अवस्थित छैन"

```

- [PostgreSQL NLS Wiki](https://wiki.postgresql.org/wiki/NLS)
- [PostgreSQL Documentation — For the Translator](https://www.postgresql.org/docs/devel/nls-translator.html)