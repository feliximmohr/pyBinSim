import logging
import zmq
import numpy as np


class InlinePoseParser(object):
	
	def __init__(self, maxChannels):

		self.log = logging.getLogger("pybinsim.InlinePoseParser")
		self.log.info("InlinePoseParser: init")

		# Default values; Stores filter keys for all channles/convolvers
		self.filtersUpdated = [True] * maxChannels

		self.defaultValue = (0, 0, 0, 0, 0, 0, 0, 0, 0)
		self.valueList = [self.defaultValue] * maxChannels

	# def parse_pose_input(self, poseData):
	def parse_pose_input(self, channel, azimuth, elevation):
		""" Compare new pose data with existing pose, determine if an update is needed """

		# we are just using first 2 elements of valueList for now
		poseData = (azimuth, elevation)

		# compare poseData with valueList for channel
		if poseData != self.valueList[channel][:len(poseData)]:
			self.filtersUpdated[channel] = True
			self.valueList[channel] = poseData + self.defaultValue[len(poseData):]            

			# self.log.info("Channel: {}".format(str(channel)))
			# self.log.info("Args: {}".format(str(poseData)))

	def is_filter_update_necessary(self, channel):
		""" Check if there is a new filter for channel """
		return self.filtersUpdated[channel]

	def get_current_values(self, channel):
		""" Return key for filter """
		self.filtersUpdated[channel] = False
		return self.valueList[channel]
