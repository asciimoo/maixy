"""Window matching shared by graphical backends."""

def nodes(tree):
    yield tree
    for child in tree.get('nodes', []) + tree.get('floating_nodes', []):
        yield from nodes(child)


def unique_window(matches, pane):
    title = pane.get('window_title', '')
    if title:
        named = [m for m in matches if title in m.get('name', '')]
        if named:
            matches = named
    if len(matches) != 1:
        raise RuntimeError('Agent window is missing or ambiguous; configure MAIXY_NAVIGATION_COMMAND for exact selection')
    return matches[0]

