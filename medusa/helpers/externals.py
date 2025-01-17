# coding=utf-8

"""Externals helper functions."""
from __future__ import unicode_literals

import logging

from medusa import app, db
from medusa.indexers.api import indexerApi
from medusa.indexers.config import indexerConfig
from medusa.indexers.exceptions import IndexerException, IndexerShowAlreadyInLibrary, IndexerUnavailable
from medusa.indexers.utils import mappings, reverse_mappings
from medusa.logger.adapters.style import BraceAdapter

from requests.exceptions import RequestException

from six import viewitems

from trakt import sync
from trakt.errors import TraktException


log = BraceAdapter(logging.getLogger(__name__))
log.logger.addHandler(logging.NullHandler())


def get_trakt_externals(externals):
    """Small trakt api wrapper, to request trakt externals using multiple external id's.

    :param externals: Dictionary of key/value pairs with external id's.
    """
    trakt_mapping = {'tvdb_id': 'tvdb', 'imdb_id': 'imdb', 'tmdb_id': 'tmdb', 'trakt_id': 'trakt'}
    trakt_mapping_rev = {v: k for k, v in viewitems(trakt_mapping)}

    for external_key in externals:
        if not trakt_mapping.get(external_key) or not externals[external_key]:
            continue

        log.debug(
            u'Looking for externals using Trakt and {indexer} id {number}', {
                'indexer': trakt_mapping[external_key],
                'number': externals[external_key],
            }
        )

        try:
            result = sync.search_by_id(externals[external_key], id_type=trakt_mapping[external_key], media_type='show')
        except (TraktException, RequestException) as error:
            log.warning('Error getting external key {external}, error: {error!r}', {
                'external': trakt_mapping[external_key], 'error': error
            })
            return {}

        if result and len(result) and result[0].ids.get('ids'):
            ids = {trakt_mapping_rev[k]: v for k, v in result[0].ids.get('ids').items()
                   if v and trakt_mapping_rev.get(k)}
            return ids
    return {}


def get_externals(show=None, indexer=None, indexed_show=None):
    """Use as much as possible sources to map known id's.

    Provide the external id's you have in a dictionary, and use as much available resources as possible to retrieve
    external id's.
    :param show: Series object.
    :param indexer: Indexer id. For example 1 for tvdb or 4 for tmdb.
    :param indexed_show: The result of a fully indexed shows. For example after an t['12345']
    """
    if show:
        indexer = show.indexer
        new_show_externals = show.externals
    else:
        if not indexer or not indexed_show:
            raise Exception('Need a minimum of a show object or an indexer + indexer_api '
                            '(Show searched through indexerApi.')
        new_show_externals = getattr(indexed_show, 'externals', {})

    # For this show let's get all externals, and use them.
    mappings = {indexer: indexerConfig[indexer]['mapped_to'] for indexer in indexerConfig}
    other_indexers = [mapped_indexer for mapped_indexer in mappings if mapped_indexer != indexer]

    # We for example want to add through tmdb, but the show is already added through tvdb.
    # If tmdb doesn't have a mapping to imdb, but tvmaze does, there is a small chance we can use that.

    for other_indexer in other_indexers:
        lindexer_api_pararms = indexerApi(other_indexer).api_params.copy()
        try:
            t = indexerApi(other_indexer).indexer(**lindexer_api_pararms)
        except IndexerUnavailable:
            continue
        if hasattr(t, 'get_id_by_external'):
            log.debug(u'Trying other indexer: {indexer} get_id_by_external',
                      {'indexer': indexerApi(other_indexer).name})
            # Call the get_id_by_external and pass all the externals we have,
            # except for the indexers own.
            try:
                new_show_externals.update(t.get_id_by_external(**new_show_externals))
            except (IndexerException, RequestException) as error:
                log.warning(
                    u'Error getting external ids for other'
                    u' indexer {name}: {reason!r}',
                    {'name': indexerApi(other_indexer).name, 'reason': error})

    # Try to update with the Trakt externals.
    if app.USE_TRAKT:
        new_show_externals.update(get_trakt_externals(new_show_externals))

    return new_show_externals


def check_existing_shows(indexed_show, indexer):
    """Check if the searched show already exists in the current library.

    :param indexed_show: (Indexer Show object) The indexed show from -for example- tvdb. It might already have some
    externals like imdb_id which can be used to search at tmdb, tvmaze or trakt.
    :param indexer: (int) The indexer id, which has been used to search the indexed_show with.
    :return: Raises the exception IndexerShowAlreadyInLibrary() when the show is already in your library.
    """
    # For this show let's get all externals, and use them.
    mappings = {indexer: indexerConfig[indexer]['mapped_to'] for indexer in indexerConfig}
    other_indexers = [mapped_indexer for mapped_indexer in mappings if mapped_indexer != indexer]

    # This will query other indexer api's.
    new_show_externals = get_externals(indexer=indexer, indexed_show=indexed_show)

    # Iterate through all shows in library, and see if one of our externals matches it's indexer_id
    # Or one of it's externals.
    for show in app.showList:

        # Check if the new shows indexer id matches the external for the show
        # in library
        if show.externals.get(mappings[indexer]) and indexed_show['id'] == show.externals.get(mappings[indexer]):
            log.debug(u'Show already in database. [{id}] {name}',
                      {'name': show.name, 'id': indexed_show['id']})
            raise IndexerShowAlreadyInLibrary('The show {0} has already been added by the indexer {1}. '
                                              'Please remove the show, before you can add it through {2}.'
                                              .format(show.name, indexerApi(show.indexer).name,
                                                      indexerApi(indexer).name))

        for new_show_external_key in list(new_show_externals):
            if show.indexer not in other_indexers:
                continue

            # Check if one of the new shows externals matches one of the
            # externals for the show in library.
            if not new_show_externals.get(new_show_external_key) or not show.externals.get(new_show_external_key):
                continue

            if new_show_externals.get(new_show_external_key) == show.externals.get(new_show_external_key):
                log.debug(
                    u'Show already in database under external ID ({existing})'
                    u' for ({id}) {name}', {
                        'name': show.name,
                        'id': show.externals.get(new_show_external_key),
                        'existing': new_show_external_key,
                    }
                )
                raise IndexerShowAlreadyInLibrary('The show {0} has already been added by the indexer {1}. '
                                                  'Please remove the show, before you can add it through {2}.'
                                                  .format(show.name, indexerApi(show.indexer).name,
                                                          indexerApi(indexer).name))


def save_externals_to_db(indexer, series_id, externals):
    """Save the indexers external id's to the db."""
    sql_l = []

    for external in externals:
        if external in reverse_mappings and externals[external] and reverse_mappings[external] != indexer:
            sql_l.append(['INSERT OR IGNORE '
                          'INTO indexer_mapping (indexer_id, indexer, mindexer_id, mindexer) '
                          'VALUES (?,?,?,?)',
                          [series_id,
                           indexer,
                           externals[external],
                           int(reverse_mappings[external])
                           ]])

    if sql_l:
        main_db_con = db.DBConnection()
        main_db_con.mass_action(sql_l)


def load_externals_from_db(indexer=None, indexer_id=None):
    """Load and recreate the indexers external id's.

    :param indexer: Optional pass indexer id, else use the current shows indexer.
    :type indexer: int
    :param indexer_id: Optional pass indexer id, else use the current shows indexer.
    :type indexer_id: int
    """
    externals = {}

    main_db_con = db.DBConnection()
    sql = ('SELECT indexer, indexer_id, mindexer, mindexer_id '
           'FROM indexer_mapping '
           'WHERE (indexer = ? AND indexer_id = ?) '
           'OR (mindexer = ? AND mindexer_id = ?)')

    results = main_db_con.select(sql, [indexer, indexer_id, indexer, indexer_id])

    for result in results:
        try:
            if result['indexer'] == indexer:
                externals[mappings[result['mindexer']]] = result['mindexer_id']
            else:
                externals[mappings[result['indexer']]] = result['indexer_id']
        except KeyError as error:
            log.error(u'Indexer not supported in current mappings: {id!r}', {'id': error})

    return externals


def show_in_library(indexer=None, indexer_id=None):
    """
    Use the load_externals_from_db method and compare it with the app.showList (library) for existing shows.

    :param indexer: Optional pass indexer id, else use the current shows indexer.
    :type indexer: int
    :param indexer_id: Optional pass indexer id, else use the current shows indexer.
    :type indexer_id: int

    :return: The show object from library if found.
    """
    externals = load_externals_from_db(indexer, indexer_id)
    if externals:
        for show in app.showList:
            for indexer, series_id in viewitems(externals):
                if reverse_mappings[indexer] == show.indexer and series_id == show.series_id:
                    return show
