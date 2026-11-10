# minimp3 — vendored, verbatim

`minimp3.h` is [lieff/minimp3](https://github.com/lieff/minimp3)'s decoder,
copied unmodified from the copy the `minimp3-sys` 0.3.2 crate bundles (sha256
`83cb0f79ab915ab328425d369a97d02ba1d0c211a862b3882852859df26a06f7`). Its
authors dedicated it to the public domain (CC0 1.0, in the file's header).

`moy_mp3.c` compiles its implementation, MPEG layer 3 only and without SIMD;
`moy_media.c` (the media service, `moy_media.h`) calls `mp3dec_decode_frame`
from its task, whose stack holds the decoder's 16.7 KB scratch (minimp3 takes
no caller's scratch), so the stack is what goes to internal SRAM with it
(`moy_media.h`, THE MEMORY).

Do not edit the copy: a fix is a new copy of upstream's file, its hash
recorded here.
