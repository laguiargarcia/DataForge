---
topic: People-and-visibility contract (who logs in to ninho, how, and what each person may open)
read_when: Changing login, adding a person, deciding which module or which data a person sees, marking data as someone's or shared, or building anything that depends on who is using the app (the wishlist's three parts, the surprise gift)
related_issues: [df-8x0m.23.6, df-8x0m.23.31, df-8x0m.23.22, df-8x0m.23.23, df-8x0m.23]
code:
  - ninho/__main__.py
status: pronto
---

# People-and-visibility contract: house server ↔ the people who use it

A starting plan, not a frozen spec (see `README.md`). Until now ninho had one shared password
(`NINHO_SENHA`): whoever had it opened everything, finances included, and the name typed at login was
only a label. This contract replaces it with people.

## What crosses

- **A person** is a row of the table `pessoas` in the app database (`NINHO_BANCO`): a login name, the
  hash of a password, and the list of modules the person may open. There are two, Lucas's and hers.
  It is a table, not two constants, because the code is public and someone else may run it.
- **Login** is a screen of ninho: a form with the person's name and password, which the phone's
  password manager fills. The server checks them once and opens a **session** (below); the password
  is not sent again. There is no HTTP Basic: the browser's own box never shows, and a request
  carrying an `Authorization: Basic` header is a request without a login.
- **No sign-up screen.** People are created, and a password is changed, by a command run on the
  server machine. The password is typed at the command's prompt, never given as an argument.
- **What a route gets from the server:** the person who is calling. A module's routes answer only to
  a person who has that module; anyone else gets 403.
- **What the screen gets:** one route that says who is logged in and which modules they have; the
  bottom bar shows only those.
- **The people of the house.** The server hands a module that needs them the list of people, id and
  name only, and gives the same list to the screen (`GET /api/pessoas`, to anyone logged in). Never
  the hash, never the modules of someone else.
- **Marking data as someone's.** A module that separates data by person stores the person's id, or
  nothing for "shared by both" (first user: the wishlist's three parts, df-8x0m.23.22).
- **Data a person may not see** (first one: the wishlist's surprise gift, df-8x0m.23.23). The
  server hides it, never only the screen; see the next section.

## The first hidden data: the surprise gift

- **What is hidden, from whom.** The "vou dar" mark on a wish of one person (table
  `desejos_presentes`: the wish, who will give it, when) is hidden from the owner of the wish. Every
  other person sees it as `presente: {de: <name>, meu: <bool>}`. A wish of both has no mark (422):
  there is nobody to hide it from.
- **How it is kept hidden.** One function of `ninho/desejos/desejos.py` (`_vista`) builds every
  wish a route answers, for the person calling; for the owner it gives `presente: null` whatever the
  mark, and a wish and its mark are read in one statement, so no change between two reads can
  show the mark to a new owner. The gift routes (`PUT`/`DELETE /api/desejos/{id}/presente`) refuse the owner
  (422) before they read the mark. The order of the list, the counts and the error texts never
  use the mark, and the app writes no log line of its own about it. One place does show it: the
  server's access log has the line of the giver's `PUT`/`DELETE .../presente`. Whoever operates the
  server machine can read that line if the log is kept; in prod the server runs with a hidden window
  and no log file (accepted, 2026-11-24, df-8x0m.23.23.1). Moving the wish to another owner deletes the mark, so the secret never
  crosses to someone who may be the giver.
- **Check.** `tests/ninho/test_desejos.py` builds the same house twice with fixed ids and times,
  once with beto's mark on ana's wish and once without, and asks every route that touches a wish as
  ana: status, content type and bytes, and her list afterwards, must be equal. A change that lets
  the mark reach the owner makes these comparisons fail.

## The session

Why: the iPhone's home-screen app does not keep HTTP Basic credentials between openings, shows the
native box slowly, and the iCloud Passwords cannot fill it (df-8x0m.23.31).

- **Entering.** `POST /api/entrar` with the JSON `{"nome": ..., "senha": ...}`. Right: 204 and the
  cookie. Wrong password, unknown name or an empty field: the same 401, with no cookie and no
  `WWW-Authenticate` header. It costs one scrypt either way. A body out of shape gets a fixed 422
  that does not say back what was sent (it would be the password). The session opens only while
  the stored hash is still the one the password was checked against, in one write transaction with
  the check: a password changed during a login opens nothing. Logging in again from a browser that
  holds a session ends that one.
- **The cookie** is `ninho_sessao`: `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`, `Max-Age` of
  90 days. Its value is a random token (at least 32 bytes from `secrets`), which means nothing by
  itself.
- **On the server** a session is a row of the table `sessoes` of the app database: the SHA-256 of
  the token (never the token), the person's id, when it was created, when it was last used and when
  it expires. It survives a restart of the server.
- **Life.** 90 days from the last use (decided by Lucas on 2026-11-24). A request with a live session pushes the
  expiry to 90 days from now and sends the cookie again, at most once a day per session, so a
  normal request writes nothing. The cookie goes with whatever the route answers, an error
  included. An expired session is a request without a login, and its row is deleted.
- **Every `/api/` route but `/api/entrar`** needs a live session: 401 without one, with no
  `WWW-Authenticate` header (it is what makes a browser show its box). The person of the session is
  read from `pessoas` on every request: a module taken from someone stops answering at once, and a
  person deleted has no session.
- **The files of the screen** (`/`, its scripts, styles and icons) are open to anyone: they hold no
  data, the code is public, and the login form has to load before there is a login.
- **Leaving.** `POST /api/sair` deletes the session's row and clears the cookie: 204, also when
  there was no session.
- **A password changed** (`python -m ninho pessoas senha`) deletes every session of that person.
  `python -m ninho pessoas sair <nome>` does only that: the answer to a lost phone.
- **A request made by another site** carries no cookie (`SameSite=Strict`), and the routes that
  write take JSON; there is no separate CSRF token.
- **The screen** asks who is logged in (`GET /api/eu`); a 401 there, or from any later call, shows
  the login form in place of the modules. The form's fields carry `autocomplete="username"` and
  `autocomplete="current-password"`. The screen has a way out ("Sair").

## Owners

- The server owns `pessoas` and the password check. A module never reads a password or the table;
  it receives the person.
- The command that creates a person or changes a password is the only other writer of `pessoas`.
  It runs on the server machine, as whoever operates it.

## Check

- Route tests with two people: each module answers 200 to a person who has it, 403 to one who does
  not, 401 without a valid login, for every route of the module.
- A wrong password, an unknown name and an empty field all answer the same 401 at `/api/entrar`.
- No answer of the server carries `WWW-Authenticate`; an `Authorization: Basic` header with a right
  name and password gets 401.
- The cookie has `HttpOnly`, `Secure`, `SameSite=Strict` and the 90 days; the table holds the hash
  of the token, not the token.
- A session used after its expiry, after `sair`, after the person's password changed or after the
  person was deleted answers 401. A session used within its life is pushed forward.
- The stored hash is never the password, and no route returns it.
- The module list of the "who am I" route matches what the routes allow.
- Data hidden from a person: the answers that person gets are byte-for-byte the same with the
  hidden data and without it (the surprise gift, above).

## Left to the implementer

The hash function (from the standard library, slow on purpose, with a salt per person), the shape
of the command, the names of columns, how the cookie is sent again when the session is pushed
forward, when expired rows are swept, and whether `/api/entrar` slows repeated wrong tries.

## Decision

Lucas, 2026-11-24:

- People in a table, two fixed ones, no sign-up screen ("a princípio é overkill mas é o certo").
- Each has their own name and password; the shared password goes away. They were first typed in
  the browser's box (HTTP Basic). Changed the same day, after the iPhone home-screen app asked for
  them at every opening and slowly (df-8x0m.23.31): a login screen of ninho and a session cookie of
  90 days, renewed by use; HTTP Basic leaves for good.
- There must be a command to change a password.
- Until the finance rule is told, her user opens the shopping list and the wishlist; finances stay
  on Lucas's user only.
- The wishlist waits for people; the surprise gift (df-8x0m.23.23) comes after both.

Still open, waiting for Lucas: the finance rule "what is shared plus what is hers" (which accounts,
which transactions she sees). Until then the split is by module, not inside finances.
