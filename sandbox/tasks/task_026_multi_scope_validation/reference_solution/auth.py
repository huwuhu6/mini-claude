def can_view(user): return user.get('role') in {'admin','auditor'}
