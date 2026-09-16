"""Version-control providers.

M3 supports GitHub only, but URL parsing and repository identity are expressed
through a provider abstraction so that adding GitLab or Bitbucket later is a new
adapter rather than a change to the service layer.
"""
