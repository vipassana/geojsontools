#!/usr/bin/env python
"""
gtfs2geojson
Converts GTFS data into GeoJSON format
Copyright 2014 Michael Farrell <http://micolous.id.au>

License: 3-clause BSD, see COPYING
"""

import geojson, argparse, csv
from datetime import timedelta
from decimal import Decimal

def gtfs_stops(stops_f, output_f):
	"""
	For each stop, convert it into a GeoJSON Point, and make all of it's attributes available.
	
	:param stops_f file: Input 'stops.txt' file from the GTFS feed.
	:param output_f file: Output GeoJSON file stream.
	"""
	stops_c = csv.reader(stops_f)

	output_layer = geojson.FeatureCollection([])
	# assume WGS84 CRS
	output_layer.crs = geojson.crs.Named('urn:ogc:def:crs:OGC:1.3:CRS84')

	header = stops_c.next()
	lat_col = header.index('stop_lat')
	lng_col = header.index('stop_lon')
	id_col = header.index('stop_id')

	for row in stops_c:
		lat, lng = Decimal(row[lat_col]), Decimal(row[lng_col])

		# make dict of other properties
		props = dict()
		for i, h in enumerate(header):
			if h in ('stop_lat', 'stop_lon'):
				continue

			if row[i] != '':
				props[h] = row[i]

		output_layer.features.append(geojson.Feature(
			geometry=geojson.Point(
				coordinates=(lng, lat)
			),
			properties=props,
			id=row[id_col]
		))
	
	geojson.dump(output_layer, output_f)

def time_as_timedelta(time):
	# We need to be able to handle values above 24:00:00, as they mean "tomorrow".
	try:
		h, m, s = (int(x) for x in time.split(':'))
	except ValueError:
		return None
	return timedelta(hours=h, minutes=m, seconds=s)

def gtfs_routes(routes_f, shapes_f, trips_f, stoptimes_f, output_f):
	"""
	For each route, convert it's 'shape' into a GeoJSON LineString, and make all
	of it's attributes available.
	
	:param routes_f file: Input 'routes.txt' file from the GTFS feed.
	:param shapes_f file: Input 'shapes.txt' file from the GTFS feed.
	:param trips_f file: Input 'trips.txt' file from the GTFS feed.
	:param stoptimes_f file: Input 'stop_times.txt' file from the GTFS feed.
	:param output_f file: Output GeoJSON file stream.
	
	"""
	
	# Load up the stop times so we can find which are the best routes.
	stoptimes_c = csv.reader(stoptimes_f)
	header = stoptimes_c.next()
	trip_id_col = header.index('trip_id')
	arrtime_col = header.index('arrival_time')
	deptime_col = header.index('departure_time')
	stopseq_col = header.index('stop_sequence')
	trip_times = {}
	for row in stoptimes_c:
		if row[trip_id_col] not in trip_times:
			# earliest seq, latest seq, earliest seq dep time, latest seq dep time
			trip_times[row[trip_id_col]] = [None, None, None, None]

		arrtime = time_as_timedelta(row[arrtime_col])
		deptime = time_as_timedelta(row[deptime_col])
		if arrtime is None or deptime is None:
			# bad data, skip!
			continue
		seq = int(row[stopseq_col])

		# Find if this is an earlier item in the sequence
		if trip_times[row[trip_id_col]][0] is None or trip_times[row[trip_id_col]][0] > seq:
			trip_times[row[trip_id_col]][0] = seq
			trip_times[row[trip_id_col]][2] = deptime

		# Find if this is an later item in the sequence
		if trip_times[row[trip_id_col]][1] is None or trip_times[row[trip_id_col]][1] < seq:
			trip_times[row[trip_id_col]][1] = seq
			trip_times[row[trip_id_col]][3] = arrtime

	# Load the shapes into a map that we can lookup.
	# We should do all the geometry processing here so that we only have to do
	# this once-off.
	shapes_c = csv.reader(shapes_f)

	header = shapes_c.next()
	shape_id_col = header.index('shape_id')
	shape_lat_col = header.index('shape_pt_lat')
	shape_lng_col = header.index('shape_pt_lon')
	shape_seq_col = header.index('shape_pt_sequence')
	shape_dist_col = header.index('shape_dist_traveled')

	shapes = {}
	shape_lengths = {}
	for row in shapes_c:
		if row[shape_id_col] not in shapes:
			shapes[row[shape_id_col]] = {}

		shapes[row[shape_id_col]][int(row[shape_seq_col])] = (Decimal(row[shape_lng_col]), Decimal(row[shape_lat_col]))

		# Calculate length according to GTFS
		# This could also be calculated by the geometry, but we trust GTFS, right...
		length = Decimal(row[shape_dist_col])
		if row[shape_id_col] not in shape_lengths or shape_lengths[row[shape_id_col]] < length:
			shape_lengths[row[shape_id_col]] = length

	# translate the shapes into a LineString for use by the GeoJSON module
	for shape_id in shapes.iterkeys():
		shape_keys = shapes[shape_id].keys()
		shape_keys.sort()
		shape = []
		for ordinal in shape_keys:
			shape.append(shapes[shape_id][ordinal])

		shapes[shape_id] = shape

	# Make a matching dict between routes and shapes
	trips = {}
	trips_ref = {}
	route_time = {}

	trips_c = csv.reader(trips_f)
	header = trips_c.next()
	route_id_col = header.index('route_id')
	shape_id_col = header.index('shape_id')
	trip_id_col = header.index('trip_id')
	for row in trips_c:
		# reference count the shapes
		if row[route_id_col] not in trips_ref:
			# route is unknown, create dict
			trips_ref[row[route_id_col]] = {}
			route_time[row[route_id_col]] = trip_times[row[trip_id_col]]

		if row[shape_id_col] not in trips_ref[row[route_id_col]]:
			# shape is unknown, create counter
			trips_ref[row[route_id_col]][row[shape_id_col]] = 0

		# increment counter
		trips_ref[row[route_id_col]][row[shape_id_col]] += 1

	# now we're done, iterate through the reference-counters and find the best
	# shape
	for route_id, candidate_shapes in trips_ref.iteritems():
		popular_shape, popular_shape_refs = None, 0
		for shape_id, refs in candidate_shapes.iteritems():
			if refs > popular_shape_refs:
				popular_shape, popular_shape_refs = shape_id, refs

		# now we should have the route's shape
		assert popular_shape is not None, 'Couldn\'t find a shape for route %r' % route_id
		trips[route_id] = popular_shape

	# Cleanup unused variables
	del trip_times

	# lets setup our output file
	output_layer = geojson.FeatureCollection([])
	# assume WGS84 CRS
	output_layer.crs = geojson.crs.Named('urn:ogc:def:crs:OGC:1.3:CRS84')

	# now we have all the shapes available, translate the routes
	routes_c = csv.reader(routes_f)
	header = routes_c.next()
	route_id_col = header.index('route_id')

	for row in routes_c:
		# make dict of other properties
		props = dict()
		for i, h in enumerate(header):
			if row[i] != '':
				props[h] = row[i]

		props['shape_id'] = trips[row[route_id_col]]
		props['shape_refs'] = trips_ref[row[route_id_col]][props['shape_id']]
		props['shape_length'] = shape_lengths[props['shape_id']]
		props['duration_sec'] = (route_time[row[route_id_col]][3] - route_time[row[route_id_col]][2]).total_seconds()

		output_layer.features.append(geojson.Feature(
			geometry=geojson.LineString(
				coordinates=shapes[trips[row[route_id_col]]]
			),
			properties=props,
			id=row[route_id_col]
		))

	# now flush the GeoJSON layer to a file.
	geojson.dump(output_layer, output_f)


def main():
	parser = argparse.ArgumentParser()
	
	parser.add_argument('-o', '--output',
		required=True,
		type=argparse.FileType('wb'),
		help='Output GeoJSON file'
	)

	group = parser.add_argument_group(title='Route conversion')
	group.add_argument('-r', '--routes',
		type=argparse.FileType('rb'),
		help='Path to agency\'s `routes.txt` file.'
	)

	group.add_argument('-s', '--shapes',
		type=argparse.FileType('rb'),
		help='Path to agency\'s `shapes.txt` file.'
	)

	group.add_argument('-t', '--trips',
		type=argparse.FileType('rb'),
		help='Path to agency\'s `trips.txt` file.'
	)

	group.add_argument('-i', '--stop-times',
		type=argparse.FileType('rb'),
		help='Path to agency\'s `stop_times.txt` file.'
	)

	group = parser.add_argument_group(title='Stop conversion')
	group.add_argument('-p', '--stops',
		type=argparse.FileType('rb'),
		help='Path to agency\'s `stops.txt` file.'
	)

	options = parser.parse_args()

	if options.routes and not options.stops:
		assert options.shapes
		assert options.trips
		assert options.stop_times

		gtfs_routes(options.routes, options.shapes, options.trips, options.stop_times, options.output)
	elif options.stops and not options.routes:
		gtfs_stops(options.stops, options.output)
	else:
		print 'invalid options combination'


if __name__ == '__main__':
	main()

