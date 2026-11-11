"""Use the account's name convention while tolerating optional profile fields."""
def display_name(user):
    first = getattr(user, 'first_name', '') or ''
    family = getattr(user, 'second_name', '') or getattr(user, 'last_name', '') or ''
    return ' '.join(filter(None, [first, family])).strip() or user.username
