import os
import logging
from nefarious.models import WatchMovie, NefariousSettings, TorrentBlacklist, WatchTVEpisode, WatchTVShow
from nefarious.parsers.movie import MovieParser
from nefarious.parsers.tv import TVParser
from nefarious.search import SearchTorrents, MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV
from nefarious.tmdb import get_tmdb_client
from nefarious.transmission import get_transmission_client
from nefarious.utils import trace_torrent_url, swap_jackett_host


class WatchProcessorBase:
    watch_media = None
    nefarious_settings: NefariousSettings = None

    tmdb_media = None
    tmdb_client = None
    transmission_client = None

    def __init__(self, watch_media_id: int, *args):
        self.nefarious_settings = NefariousSettings.objects.all().get()
        self.tmdb_client = get_tmdb_client(self.nefarious_settings)
        self.transmission_client = get_transmission_client(self.nefarious_settings)
        self.watch_media = self._get_watch_media(watch_media_id)
        self.tmdb_media = self._get_tmdb_media()

    def fetch(self):
        search = self._get_search_results()
        valid_search_results = []

        if search.ok:

            for result in search.results['Results']:
                if self._is_match(result['Title']):
                    valid_search_results.append(result)
                else:
                    logging.info('Not matched: {}'.format(result['Title']))

            if valid_search_results:

                while valid_search_results:

                    # find the torrent result with the highest weight (i.e seeds)
                    best_result = self._get_best_torrent_result(valid_search_results)

                    # add to transmission
                    transmission_client = get_transmission_client(self.nefarious_settings)
                    transmission_session = transmission_client.session_stats()

                    torrent = transmission_client.add_torrent(
                        best_result['torrent_url'],
                        paused=True,  # start paused so we can verify this torrent hasn't been blacklisted - then start it
                        download_dir=self._get_download_dir(transmission_session)
                    )

                    # verify it's not blacklisted and save & start this torrent
                    if not TorrentBlacklist.objects.filter(hash=torrent.hashString).exists():
                        logging.info('Adding torrent for {}'.format(self.tmdb_media[self._get_tmdb_title_key()]))
                        logging.info('Adding torrent {} with {} seeders'.format(best_result['Title'], best_result['Seeders']))
                        logging.info('Starting torrent id: {}'.format(torrent.id))

                        # save torrent details on our watch instance
                        self._save_torrent_details(torrent)

                        # start the torrent
                        torrent.start()
                        return True
                    else:
                        # remove the blacklisted/paused torrent and continue to the next result
                        logging.info('BLACKLISTED: {} ({}) - trying next best result'.format(best_result['Title'], torrent.hashString))
                        transmission_client.remove_torrent([torrent.id])
                        valid_search_results.pop()
            else:
                logging.info('No valid search results for {}'.format(self.tmdb_media[self._get_tmdb_title_key()]))
        else:
            logging.info('Search error: {}'.format(search.error_content))

        return False

    def _get_best_torrent_result(self, results: list):
        best_result = None
        valid_search_results = []

        for result in results:

            # try and obtain the torrent url (it can redirect to a magnet url)
            try:
                # add a new key to our result object with the traced torrent url
                result['torrent_url'] = result['MagnetUri'] or trace_torrent_url(
                    swap_jackett_host(result['Link'], self.nefarious_settings))
            except Exception as e:
                logging.info('Exception tracing torrent url: {}'.format(e))
                continue

            # add torrent to valid search results
            logging.info('Valid Match: {} with {} Seeders'.format(result['Title'], result['Seeders']))
            valid_search_results.append(result)

        if valid_search_results:

            # find the torrent result with the highest weight (i.e seeds)
            best_result = valid_search_results[0]
            for result in valid_search_results:
                if result['Seeders'] > best_result['Seeders']:
                    best_result = result

        else:
            logging.info('No valid best search result')

        return best_result

    def _get_watch_media(self, watch_media_id: int):
        raise NotImplementedError

    def _get_download_dir(self, transmission_session):
        raise NotImplementedError

    def _get_tmdb_media(self):
        raise NotImplementedError

    def _get_parser_class(self):
        raise NotImplementedError

    def _get_tmdb_title_key(self):
        raise NotImplementedError

    def _get_media_type(self) -> str:
        raise NotImplementedError

    def _save_torrent_details(self, torrent):
        self.watch_media.transmission_torrent_id = torrent.id
        self.watch_media.transmission_torrent_hash = torrent.hashString
        self.watch_media.save()

    def _is_match(self, title: str) -> str:
        raise NotImplementedError

    def _get_search_results(self):
        raise NotImplementedError


class WatchMovieProcessor(WatchProcessorBase):

    def _is_match(self, title):
        parser_class = self._get_parser_class()
        parser = parser_class(title)
        return parser.is_match(self.tmdb_media[self._get_tmdb_title_key()])

    def _get_media_type(self) -> str:
        return MEDIA_TYPE_MOVIE

    def _get_download_dir(self, transmission_session):
        return os.path.join(
            transmission_session.download_dir, self.nefarious_settings.transmission_movie_download_dir.lstrip('/'))

    def _get_parser_class(self):
        return MovieParser

    def _get_tmdb_title_key(self):
        return 'original_title'

    def _get_tmdb_media(self):
        movie_result = self.tmdb_client.Movies(self.watch_media.tmdb_movie_id)
        movie = movie_result.info()
        return movie

    def _get_watch_media(self, watch_media_id: int):
        watch_movie = WatchMovie.objects.get(pk=watch_media_id)
        return watch_movie

    def _get_search_results(self):
        media = self.tmdb_media
        return SearchTorrents(MEDIA_TYPE_MOVIE, media[self._get_tmdb_title_key()])


class WatchTVProcessorBase(WatchProcessorBase):

    def _get_watch_media(self, watch_media_id: int):
        watch_movie = WatchTVEpisode.objects.get(pk=watch_media_id)
        return watch_movie

    def _get_media_type(self) -> str:
        return MEDIA_TYPE_TV

    def _get_download_dir(self, transmission_session):
        return os.path.join(
            transmission_session.download_dir, self.nefarious_settings.transmission_tv_download_dir.lstrip('/'))

    def _get_parser_class(self):
        return TVParser

    def _get_tmdb_title_key(self):
        return 'name'


class WatchTVEpisodeProcessor(WatchTVProcessorBase):
    """
    Single episode
    """

    def _get_watch_media(self, watch_media_id: int):
        watch_episode = WatchTVEpisode.objects.get(pk=watch_media_id)
        return watch_episode

    def _is_match(self, title):
        parser_class = self._get_parser_class()
        parser = parser_class(title)
        return parser.is_match(
            self.tmdb_media[self._get_tmdb_title_key()],
            self.tmdb_media['season_number'],
            self.tmdb_media['episode_number'],
        )

    def _get_tmdb_media(self):
        episode_result = self.tmdb_client.TV_Episodes(self.watch_media.watch_tv_show.tmdb_show_id, self.watch_media.season_number, self.watch_media.episode_number)
        episode = episode_result.info()
        return episode

    def _get_search_results(self):
        # query the show for this episode
        watch_episode = self.watch_media  # type: WatchTVEpisode
        show_result = self.tmdb_client.TV(watch_episode.watch_tv_show.tmdb_show_id)
        show = show_result.info()
        return SearchTorrents(MEDIA_TYPE_TV, show['name'])


class WatchTVShowProcessor(WatchTVProcessorBase):
    """
    Entire season
    """
    season_number = None

    def __init__(self, watch_media_id: int, season_number: int):
        self.season_number = season_number
        super().__init__(watch_media_id)

    def _get_watch_media(self, watch_media_id: int):
        watch_show = WatchTVShow.objects.get(pk=watch_media_id)
        return watch_show

    def _is_match(self, title):
        parser_class = self._get_parser_class()
        parser = parser_class(title)
        return parser.is_match(
            self.tmdb_media[self._get_tmdb_title_key()],
            self.season_number,
        )

    def _get_tmdb_media(self):
        show_result = self.tmdb_client.TV(self.watch_media.tmdb_show_id)
        show = show_result.info()
        return show

    def _get_search_results(self):
        media = self.tmdb_media
        return SearchTorrents(MEDIA_TYPE_TV, media[self._get_tmdb_title_key()])

    def _save_torrent_details(self, torrent):
        # they'll all be the same transmission id since it's a full season torrent
        for watch_tv_episode in self.watch_media.watchtvepisode_set.all():
            watch_tv_episode.transmission_torrent_id = torrent.id
            watch_tv_episode.transmission_torrent_hash = torrent.hashString
            watch_tv_episode.save()
