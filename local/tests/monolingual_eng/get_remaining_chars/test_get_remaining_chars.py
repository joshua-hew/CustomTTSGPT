import json
import logging
import unittest
import unicodedata
from unidecode import unidecode

# Setup file logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Log everything at DEBUG level and above

# Setup logging to file for detailed debug information
file_handler_debug = logging.FileHandler('logs/debug_log.log', mode='w')
file_handler_debug.setLevel(logging.DEBUG)
file_handler_debug.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Add handlers to the logger
logger.addHandler(file_handler_debug)



def get_remaining_chars_to_send(chars_to_send: list, chars_received: list) -> list:
    """Returns an array of the remaining characters that haven't been converted to speech."""

    def remove_accents(input_str):
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return ''.join([c for c in nfkd_form if not unicodedata.combining(c)])

    def replace_special_quotes(char_array):
        special_quotes = {'\u2018': '"', '\u2019': '"', '\u201c': '"', '\u201d': '"'}
        return [special_quotes.get(char, char) for char in char_array]

    def replace_newlines(arr):
        result = []
        for char in arr:
            if char == "\n":
                if not result or result[-1] != " ":
                    result.append(" ")
            else:
                result.append(char)
        return result

    # Log inputs for troubleshooting
    logger.debug(f"Characters to send. Len: {len(chars_to_send)}.")
    logger.debug(f"{json.dumps(chars_to_send)}")
    logger.debug(f"Characters received. Len: {len(chars_received)}.")
    logger.debug(f"{json.dumps(chars_received)}")


    # Format chars to send for easier comparison with characters received
    # chars_to_send_formatted = [remove_accents(c) for c in chars_to_send]    # Normalize the chars_to_send. Change special characters to unicode base chars. ex: "é" -> "e".
    # chars_to_send_formatted = replace_special_quotes(chars_to_send_formatted)
    chars_to_send_formatted = [unidecode(c) for c in chars_to_send]

    # Format chars received for easier comparison
    chars_received_formatted = chars_received[1:]   # Remove leading space in chars_received
    
    # Log formatted versions for troubleshooting
    logger.debug(f"Characters to send formatted. Len: {len(chars_to_send_formatted)}.")
    logger.debug(f"{json.dumps(chars_to_send_formatted)}")
    logger.debug(f"Characters received formatted. Len: {len(chars_received_formatted)}.")
    logger.debug(f"{json.dumps(chars_received_formatted)}")


    # Determine where to continue in the text queue.
    # Continue point is the index after the last character received succesfully. 
    continue_point = None
    
    # The index in the second array.
    # This is where the matching character should be in the second array.
    # The matching character is allowed to be at most (j + displacement tolerance away)
    j = 0
    displacement_tolerance = 2

    for i in range(len(chars_to_send_formatted)):
        # The character to match
        char = chars_to_send_formatted[i]

        if char == "\n":
            continue

        # If j pointer in bounds:
        if j < len(chars_received_formatted):
            
            # The matching character is allowed to be at most (j + displacement tolerance away)
            # If the matching character is not exactly at j, but within the tolerance, update j, and log that occurrence
            # Else, throw an error and populate logs
            try:
                index_of_next_matching_char = chars_received_formatted.index(char, j)
                if index_of_next_matching_char == j:
                    pass
                
                elif index_of_next_matching_char - j <= displacement_tolerance:
                    logger.warning(f"Idiosyncracy found. Expected matching char '{char}' to be at {j}. Was found at {index_of_next_matching_char}")
                    logger.debug(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                    logger.debug(f"Context for chars_received: {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")

                    # Set j to index of matching char 
                    j = index_of_next_matching_char
                
                else:
                    logger.error(f"Index of next matching char not within tolerance. Expected char '{char}' to be at {j}. Was found at {index_of_next_matching_char}. Displacement tolerance: {displacement_tolerance}")
                    logger.error(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                    logger.error(f"Context for chars_received (centered at j): {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")
                    logger.error(f"Context for chars_received (centered at index of next_next_matching_char): {json.dumps(chars_received_formatted[max(0, index_of_next_matching_char-10):index_of_next_matching_char+10])}")
                    raise Exception(f"Could not confirm that the current char was received within the displacement tolerance. Char: '{char}'")

                # Increment j
                j += 1
            

            except ValueError as e: # If can't find matching char in chars received
                logger.warning(f"Index() could not find a match for char '{char}' at position {i}")
                logger.debug(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                logger.debug(f"Context for chars_received (centered at j): {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")
                continue_point = i
                break


        else: # If reached end of received chars, then the current un-matched char is the continue point
            logger.info("Found continue point")
            continue_point = i
            break


    remaining_chars = chars_to_send[continue_point:]

    logger.debug(f"Remaining chars. Len {len(remaining_chars)}")
    logger.debug(f"{json.dumps(remaining_chars)}")
    return remaining_chars

    

class TestGetRemainingCharacters(unittest.TestCase):

    def test_01(self):
        """ Test that remaining chars found in a typical retry context. """
        logger.info("Running test 01")

        with open('inputs/01/chars_to_send.json', 'r') as f:
            chars_to_send = json.load(f)

        with open('inputs/01/chars_received.json', 'r') as f:
            chars_received = json.load(f)
        
        with open('inputs/01/remaining_chars.json', 'r') as f:
            remaining_chars = json.load(f)
        
        val = get_remaining_chars_to_send(chars_to_send, chars_received)

        self.assertEqual(val, remaining_chars)
    
    
    def test_02(self):
        """ Test that function accounts for special characters like fancy right quotation marks. """
        logger.info("Running test 02")

        with open('inputs/02/chars_to_send.json', 'r') as f:
            chars_to_send = json.load(f)

        with open('inputs/02/chars_received.json', 'r') as f:
            chars_received = json.load(f)
        
        with open('inputs/02/remaining_chars.json', 'r') as f:
            remaining_chars = json.load(f)
        
        val = get_remaining_chars_to_send(chars_to_send, chars_received)

        self.assertEqual(val, remaining_chars)
    
    
    def test_03(self):
        """ Test that remaining chars starts with the first unmatched char in the event that index() could not find matching char. """
        logger.info("Running test 03")

        with open('inputs/03/chars_to_send.json', 'r') as f:
            chars_to_send = json.load(f)

        with open('inputs/03/chars_received.json', 'r') as f:
            chars_received = json.load(f)
        
        with open('inputs/03/remaining_chars.json', 'r') as f:
            remaining_chars = json.load(f)
        
        val = get_remaining_chars_to_send(chars_to_send, chars_received)

        self.assertEqual(val, remaining_chars)



if __name__ == '__main__':
    unittest.main()