// The UI API's prop check (moy_uitree_check.c), apart from the engine so a
// front with no engine (the host checker's bare Lua) takes it alone.
#ifndef MOY_UITREE_CHECK_H
#define MOY_UITREE_CHECK_H

#include <stddef.h>

// One table, the contract's (moy_uitree_schema.h, generated from
// tests/uitree_contract/contract.json), every front checks a node's props
// against: no engine is needed, so a front checks at the call that names the
// prop and raises at that call's line. A value's class as its front sees it:
enum { UIT_VC_ANY = 0, UIT_VC_TEXT, UIT_VC_INT, UIT_VC_BOOL, UIT_VC_LIST, UIT_VC_DICT, UIT_VC_FN, UIT_VC_NUM };
// or'ed into a class: the front builds this kind or prop although the spec
// only designs it, so it is checked as a built one
#define UIT_VC_BUILDS 0x10
// NULL where kind `kind` takes prop `prop` with a value of class `vc`
// (UIT_VC_ANY: not checked) whose text is `word` (NULL: not a text). Else the
// spec's code ("N01" no such kind or prop, a designed one, a word not the
// prop's, a value of a class its type does not take, a text that is no
// template given to an int; "N04" a retired prop; "T03" an int prop given
// another class), with `out` holding WHAT, EXPECTED and FIX, each ended by a
// 0x1F (an empty one where the message has none).
const char *uit_check_prop(const char *kind, const char *prop, int vc, const char *word, char *out, size_t n);
// The words of word prop `prop` of kind `kind`, comma-separated, or NULL.
const char *uit_prop_words(const char *kind, const char *prop);
// The contract's name of prop `prop`'s type on kind `kind` ("tpl", "cond",
// "int", "word", "list:tpl", ...), or NULL where the kind has no such prop.
const char *uit_prop_type(const char *kind, const char *prop);

#endif
