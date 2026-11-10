"""Where the browser's console keeps things in its VFS: the one place the
worker (worker.js, which asks for them once the VM is up) and the runner's
modules take a path from, so the page and the VM never name one twice.

    CARTS    the carts folder; the sync registry places every other root
             beside it (moy_sync.roots_json)
    SEED     where a site-mode boot stages the served bundle for
             moy_sync.seed_store, removed once it is read
    SPOOL    the worker's spool: a fetch's answer and a picked file
             (carts_link), Music's segments (media_link), each at the path
             its job names
    LISTING  the songs a page plays and does not keep, as the worker lists
             them (media_link)
    DROP     a dropped file while its importer reads it (web_boot), which
             removes it after: a stray drop under the carts folder would be
             swept as a cart
    EXPORT   a cart packed for the page to take
"""

CARTS = "/moy/carts"
SEED = "/seed"
SPOOL = "/moy/net"
LISTING = SPOOL + "/media.json"
DROP = "/moy/tmp/drop"
EXPORT = "/moy/tmp/export.zip"
