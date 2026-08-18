"""Starter file for the offline dependency benchmark."""

import pygame


def main():
    """Create a minimal window after the dependency is available."""
    screen = pygame.display.set_mode((320, 240))
    pygame.display.set_caption("mini-claude benchmark")
    screen.fill((24, 32, 48))
    pygame.display.flip()


if __name__ == "__main__":
    main()
