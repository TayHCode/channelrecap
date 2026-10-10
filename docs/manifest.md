# Manifest reference

Use a UTF-8 CSV file with a header. The `url` column is an alias for `source`;
including both is an error. Rows with an empty source are skipped. A byte-order mark is accepted. Blank rows are
skipped. Unknown columns produce a warning and are ignored. All rows are validated
before work starts; errors include the row number, with the header counted as row 1.

| Column | Required | Meaning and default |
|---|---|---|
| `source` | Yes | A link beginning with `http://` or `https://`, or an existing local file. Relative paths start at the CSV folder. `.vtt` and `.srt` are captions, regardless of case; supported audio/video extensions are treated as media. |
| `start` | No | Start time in the original source; blank means the beginning. |
| `end` | No | End time in the original source; blank means the end. Must be greater than the start. |
| `title` | No | Display title; otherwise the link title, file stem or `Untitled`. |
| `date` | No | Publication date as `YYYY-MM-DD`; otherwise the link's upload date or blank. |
| `language` | No | Language code such as `en`; overrides `--language`. Otherwise speech auto-detection. |

```csv
source,start,end,title,date,language
https://example.com/watch?v=YOUR_VIDEO_ID,,,My first video,2026-01-10,en
https://example.com/watch?v=YOUR_STREAM_ID,00:12:30,00:58:00,Live Q&A (main part),2026-02-14,en
recordings/workshop.mp4,,,Workshop recording,2026-03-21,
recordings/podcast-episode.m4a,00:01:15,,,,
captions/tutorial.vtt,,,Tutorial,2026-04-04,en
```

Replace placeholders with your own videos or files before running:

```sh
channelrecap run --manifest videos.csv --out my-library
```

Time codes accept seconds (`90`, `90.5`), minutes and seconds (`01:30`, `75:30`),
or hours, minutes and seconds (`01:02:03.5`). Colon-form seconds must be below 60;
minutes in the three-part form must also be below 60. Negative values, words and
four-part times are invalid. Times are always in the original source, including when
reading a clip's transcript later.

For media, ffmpeg clips before transcription. For captions, a cue is kept if its start
is at or after `start` and before `end`. A cue that starts before the range is excluded
even if it overlaps. VTT notes, styles and regions are ignored. Tags are stripped,
common entities decoded and whitespace collapsed; empty and adjacent identical cues
are dropped. Captions carry no word-level timing or language probability.

Missing local files, bad dates or time codes, duplicate rows and an
empty usable manifest cause exit 2 before output changes. Unsupported types exit 4.
Recognizable playlist and channel URLs are rejected during validation (exit 2).
Site-specific collection links identified later by yt-dlp, and live or upcoming
streams, fail only their row. Finished streams work like other videos.
Each row is one video or one time range; there is no login, cookies or site crawling.

The row identifier uses the first eight hex characters of SHA-256 over the exact `source` text,
start and end separated by newlines. Present times have three decimal places; absent
times are empty. The video id combines a title slug (up to 60 characters) and the key.
Once assigned it is kept in `library.json`, even if the title later changes. Two
ranges from the same link share downloaded audio during that run.

You may instead pass local files or links as positional sources:
`channelrecap run talk.mp4 notes.vtt --out my-library`. These have no time codes or dates;
paths start at the current folder. Do not combine positional sources with `--manifest`.
Finished rows are skipped unless `--force`; `--limit N` applies to pending rows.

One output folder is one library. Previously finished rows stay in it even if omitted
from a later manifest. Use a separate output folder to analyze a different selection.

Validation is all-or-nothing: a missing/unreadable file, invalid field or range,
unsupported type, or recognizable channel/playlist URL prevents every row from
starting. Correct the manifest and rerun. Channel/playlist rows are not crawled;
list each individual video URL. Site downloads require yt-dlp and network access.
Other site-specific channel/playlist links are refused if yt-dlp identifies them
as such. After validation, processing failures affect only their own rows unless
`--fail-fast` is selected.
