# Styles

This folder holds New Book of Names' styles: the ones that come with it,
and the ones you save. The interface lists every style here in its Style
box, and the command line finds one by its name: `generate akkadian` uses
`Akkadian.toml`.

A style is a set of example names for one kind of name, such as the names
of a people or their places, optionally with a title, a description and
pinned settings. It can be:

- a plain list of names (`.txt`), one name per line, named after the file;
- a style file (`.toml`), with a title and description, any pinned
  settings, and one or more lists of names, such as male names, female
  names and places. Only the names are required.

If a style file and a plain list have the same name, the style file is
used.

Settings a style doesn't pin are worked out from its names each time it is
opened. Saving in the interface, or `create` on the command line, writes a
style file here.
