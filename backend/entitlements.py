"""Subscription entitlement logic for SingoLing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database import User, Song, Playlist


def is_subscription_active(user: User) -> bool:
    """Check if user has valid active subscription."""
    if not user.subscription_tier or user.subscription_tier == 'free':
        return False
    
    if user.subscription_tier == 'lifetime':
        return True
    
    if user.subscription_status != 'active':
        return False
    
    if user.subscription_expires_at and user.subscription_expires_at < datetime.now(timezone.utc):
        return False
    
    return True


def can_play_music(user: User, song: Song) -> bool:
    """Music playback always allowed (YouTube/Apple Music compliance)."""
    return True


def can_access_lyrics(user: User, song: Song, playlist: Playlist | None, position_in_playlist: int | None) -> bool:
    """Determine if user can see interactive lyrics/translations.
    
    Args:
        user: Current user
        song: Song being accessed
        playlist: Playlist context (may be None if song accessed directly)
        position_in_playlist: 1-indexed position of song in playlist (None if not in playlist context)
    
    Returns:
        True if user can access lyrics, False otherwise
    """
    # Premium/lifetime: full access
    if user.subscription_tier in ['premium', 'lifetime', 'premium_student']:
        if is_subscription_active(user):
            return True
    
    # Language-specific tier: match source language
    if user.subscription_tier == song.language_code:
        if is_subscription_active(user):
            return True
    
    # Free tier: first 2 songs per playlist (positions 0 and 1)
    if playlist and position_in_playlist is not None:
        return position_in_playlist < 2
    
    # If no playlist context, deny access for free users
    return False


def get_upgrade_cta(user: User, song: Song, playlist: Playlist | None) -> dict:
    """Generate context-aware upgrade messaging for lyrics lock screen.
    
    Args:
        user: Current user
        song: Song being accessed
        playlist: Playlist context (may be None)
    
    Returns:
        Dictionary with upgrade CTA details
    """
    # Get first song in playlist for "back to trial" navigation
    first_song_id = None
    back_to_trial_url = '/'
    
    if playlist:
        # Get songs from playlist, ordered by position
        if hasattr(playlist, 'songs') and playlist.songs:
            # Assuming songs are already sorted by position
            first_song_id = playlist.songs[0].song_id if hasattr(playlist.songs[0], 'song_id') else playlist.songs[0].id
            back_to_trial_url = f'/playlist/{playlist.id}/song/{first_song_id}'
        else:
            back_to_trial_url = f'/playlist/{playlist.id}'
    
    if user.subscription_tier == 'free' or not user.subscription_tier:
        return {
            'title': 'Unlock Interactive Lyrics',
            'message': 'Upgrade to Premium for unlimited lyrics, translations, and word definitions across all songs.',
            'cta': 'See Premium Plans',
            'url': '/pricing',
            'back_to_trial_url': back_to_trial_url,
            'highlight_features': [
                'Interactive word-by-word translations',
                'Instant definitions with keyboard shortcuts',
                'Full-line translations',
                'Unlimited songs in all languages'
            ]
        }
    
    if user.subscription_status == 'past_due':
        return {
            'title': 'Payment Issue',
            'message': 'Update your payment method to continue learning.',
            'cta': 'Update Payment',
            'url': '/account',
            'back_to_trial_url': back_to_trial_url,
            'highlight_features': []
        }
    
    if user.subscription_status == 'canceled':
        return {
            'title': 'Subscription Ended',
            'message': 'Renew to regain full access to interactive lyrics.',
            'cta': 'Renew Subscription',
            'url': '/pricing',
            'back_to_trial_url': back_to_trial_url,
            'highlight_features': []
        }
    
    # Default fallback
    return {
        'title': 'Lyrics Locked',
        'message': 'This song requires an active subscription.',
        'cta': 'Manage Subscription',
        'url': '/account',
        'back_to_trial_url': back_to_trial_url,
        'highlight_features': []
    }
