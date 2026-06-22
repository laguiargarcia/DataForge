from concurrent.futures import ThreadPoolExecutor, as_completed

from .channels import Channel


def deliver_all(
    targets: list[tuple[Channel, str]],
    *, subject: str, body: str, env: dict[str, str],
) -> None:
    """Run each (channel, recipient) send concurrently. Raise if any fails."""
    if not targets:
        raise ValueError("no channels configured for message task")
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        futures = {
            ex.submit(channel.send, recipient, subject, body, env): channel.name
            for channel, recipient in targets
        }
        for fut in as_completed(futures):
            ch_name = futures[fut]
            try:
                fut.result()
            except Exception as e:
                failures.append(f"{ch_name}: {e}")
    if failures:
        raise RuntimeError(
            f"channel(s) failed (strict-all): {'; '.join(failures)}"
        )
