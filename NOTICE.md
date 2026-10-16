# Code provenance

Neko Video is an independent repository, not a GitHub fork. That describes
repository ownership, **not** a claim that every implementation originated here.

The Bilibili/Douyin response formats and official-summary integration originate
in [YukiSakiko/content_understanding_plugin](https://github.com/YukiSakiko/content_understanding_plugin),
whose plugin manifest declares MIT. The starting maintained implementation was
[znzsofficial/content_understanding_plugin](https://github.com/znzsofficial/content_understanding_plugin)
at `03254c5` (2.2.1). Original authorship is retained in LICENSE.

The bounded media lookup, image preview, chat-scoped handles and delivery guards
were developed in that maintained implementation. Their implementations and tests
are retained and reorganized here. Official providers, credential persistence,
presentation, lifecycle and SDK adapters are separated rather than distributed as
the original monolithic plugin class.

The two third-party parser response formats were supplied as Android client
examples. No Android UI, third-party token, account cookie, runtime configuration
or user data is included. Parser services are external dependencies, not services
owned or endorsed by this project.
