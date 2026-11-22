Fix this issue, reported by a user of this library:

---

**Columns don't line up when cells contain Japanese**

I use tablefmt to list files in a terminal. Whenever a file name contains Japanese, the columns after it shift and nothing lines up anymore:

```
name          | KB | modified
--------------+----+-----------
レポート_2024.pdf | 12 | 2024-11-02
notes.txt     |  3 | 2024-11-20
議事録.docx      | 48 | 2024-10-15
```

Long names that I cut short with `max_width` come out wrong as well. The file names come straight from a shared folder on a Mac, if that matters. Could you fix this?
