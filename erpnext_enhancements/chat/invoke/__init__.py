# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``@triton`` invocation — two origins, one handler.

| module | what it is |
|---|---|
| ``envelope`` | **pure.** The frozen contract, its canonical form, and the derived request id |
| ``normalize`` | both origins → one envelope, and the origin recorded but not passed on |
| ``handler`` | the one handler. Consumes envelopes; has no origin to branch on |
| ``triton_client`` | the model turn, carrying the mentioning person's identity |
| ``dispatch`` | acknowledge, dedupe, enqueue — inside Google's 30-second deadline |

The whole package exists to make one sentence structurally true: **the same question asked
from the SPA and from the native Chat client produces the same answer, through the same code.**
Not "the handler must not branch on origin" — there is no origin in the envelope to branch on.

Nothing here writes a message row on the inbound side. That is the sync engine's job, and it
already owns the echo ladder, the idempotency keys and the ordering rules; a second writer
would be a second place ``unique(room, client_message_id)`` has to be got right. The only row
this package writes is Triton's own reply, and it writes it through the same
``outbox.insert_message`` a human message goes through.
"""
