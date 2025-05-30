# A file containing all classes required to create the data structures that allow for decoding instructions based on the Arm Specification

import xml.etree.ElementTree as et
import sys
from common import *
from disassembler import *
import pickle
import elftools # pip install pyelftools
from elftools.elf.elffile import ELFFile


class EncodingTable():
    """
    A table within the greater EncodingTable structure. Stores further EncodingTables or InstructionTables based on instruction variable values in a tree-like structure.

    Attributes:
        entries - A mapping of the variable values to either an instruction or nested encoding table. Each key is a flattened map of variable names to the matching patterns.
        instructionMapping - the InstructionMapping for this table, used to extract the correct variables from instructions that pass through.
        directFile - the direct file path in the case of there only being one entry in the table.
    """

    def __init__(self, root, hierarchy, sect=False):
        """
        Initialises the EncodingTable object

        :param root: The root node of the encodingindex.xml file that the table is generated from
        :param hierarchy: The node that this table is being generated from
        :param sect: Whether this table is representing an iclass_sect or node
        """
        self.entries = {}
        self.instructionMapping = None
        self.directFile = None # Used only in cases where an iclass_sect has no table and is just one instruction name
        # Handles regular tables and iclass_sects differently
        # sect: this Encoding Table is a table where each entry links to the iformfile of a specific instruction.

        variables = {}
        # Use the regdiagram to create instructionMapping for this table
        regdiagram = hierarchy.find("regdiagram")
        boxes = regdiagram.findall("box")
        for box in boxes:
            if "name" in box.attrib:
                if "width" in box.attrib:
                    varWidth = box.attrib["width"]
                else:
                    varWidth = 1 #For some reason doesnt declare 1 width if the width is 1
                variables[box.attrib["name"]] = [int(box.attrib["hibit"]), int(varWidth)]
        self.instructionMapping = InstructionMapping(variables)

        # If EncodingTable is representing an iclass_sect
        if sect:
            instructiontable = hierarchy.find("instructiontable")

            tableVars = []
            headers = instructiontable.find("thead").findall("tr")

            # Special case if the table only has one row - just set a directFile
            if len(headers) == 1:
                tr = instructiontable.find("tbody").find("tr")
                # Go into tbody, go into first tr, then first td. This contains the iformid!
                if "iformfile" in tr.attrib:
                    self.directFile = InstructionPage(ARM_FILE_PATH + "/" + tr.attrib["iformfile"])
                else:
                   self.directFile = tr.attrib["encname"]
                return

            # Get the tableVars in order by reading the text of headers's
            ths = headers[1].findall("th") 
            for th in ths:
                tableVars.append(th.text)

            # Enter tbody, which is the body of the table
            body = instructiontable.find("tbody")
            # For each row of the table
            for tr in body.findall("tr"):
                mapping = []
                # Find all columns in the row, and iterate through them
                tds = tr.findall("td")
                for i in range(0, len(tableVars)):
                    # Add to mapping, tuples of (varname, expected value)
                    mapping.append((tableVars[i], tds[i].text))
                # If a file exists, set the mapping to the filename, otherwise encname
                if "iformfile" in tr.attrib:
                    self.entries[tuple(mapping)] = InstructionPage(ARM_FILE_PATH + "/" + tr.attrib["iformfile"])
                else:
                    self.entries[tuple(mapping)] = tr.attrib["encname"]
        # a node, not an iclass_sect. so handle accordingly, creating further encodingtable objects in the entries
        else:
            # Iterate through each node, adding their entry to the table
            nodes = hierarchy.findall("node")
            for node in nodes:
                mapping = []
                decode = node.find("decode")
                boxes = decode.findall("box")
                # Create each table entry through reading the decode section
                for box in boxes:
                    name = box.attrib["name"]
                    value = box.find("c").text
                    mapping.append((name, value))
                # If a groupname, create an dict of the mapping from the decode, then add to entries, with the value being a newly defined encodingtable with the xml parsed
                if "groupname" in node.attrib:
                    self.entries[tuple(mapping)] = EncodingTable(root, node)
                # If an iclass, find the iclass_sect it corresponds to and create an EncodingTable based on it
                elif "iclass" in node.attrib:
                    iclass_sects = root.findall(".//iclass_sect")
                    found = False
                    for sect in iclass_sects:
                        if sect.attrib["id"] == node.attrib["iclass"]:
                            found = True
                            self.entries[tuple(mapping)] = EncodingTable(root, sect, True)
                            continue
                    # If not found, no sect for this iclass
                    if not found:
                        self.entries[tuple(mapping)] = node.attrib["iclass"]

    def print(self):
        """
        Prints the encoding table
        """
        print(len(self.entries.values()))
        for entry in self.entries.values():
            print(entry)
        for entry in self.entries.values():
            if type(entry) is EncodingTable:
                print("")
                entry.print()

    def decode(self, instruction):
        """
        Given an instruction, decode it by finding the correct entry in the entires attribute, and passing it down to further levels of the table. If the correct instruction is found, disassemble it and return the disassembled instruction.

        :param instruction: The instruction to disassemble
        """
        # Extract variables from the instruction
        values = self.instructionMapping.assignValues(instruction)

        # If there is no table, handle special case and directly assign directFile
        if self.directFile is not None:
            if type(self.directFile) is EncodingTable: # this will NEVER occur, as directfile only occurs due to a quirk of the structure with instructions, but included for completeness
                return self.directFile.decode(instruction)
            elif type(self.directFile) is InstructionPage:
                # Return either name or the matched InstructionPage
                return self.directFile.disassemble(instruction)
            else:
                return self.directFile
        

        # For each row of the encoding table, checks if each variable assignment of the row matches a variable in the instruction being matched
        for row in self.entries.keys():
            matches = True
            # Check if every variable in the instruction matches an instructon in the table entry
            for tup in row:
                if not self.matchVar(values, tup): # Checks if any of the values (variable values extracted from the instruction based on the encoding) match the variable in the key's tuple
                    matches = False
            if matches:
                # This is the correct row
                if type(self.entries[row]) is EncodingTable:
                    return self.entries[row].decode(instruction)
                elif type(self.entries[row]) is InstructionPage:
                    # Return either name or the matched InstructionPage
                    return self.entries[row].disassemble(instruction)
                else:
                    return self.entries[row]
        return None

    def matchVar(self, vars, tup):
        """
        Finds if the variable with the same name as the given tuple has the same value. Accounts for != symbols declaring portions of the value should NOT be the same.

        :param vars: all variables and their extracted values from the InstructionMapping
        :param tup: a single (variable name, value) tuple, which will be checked to see if it matches a variable in vars
        """
        # Check each var
        for var in vars:
            if var[0] == tup[0]:
                # Check if var[1] matches tup[1]
                if tup[1] == None:
                    return True
                # Check if each element matches. If a != is present, make sure the remainder of the string is not equal to the rest of it.
                if "!=" in tup[1]:
                    splitEncoding = tup[1].replace(" ", "").split("!=")
                    if len(splitEncoding[0]) == 0:
                        return not compareWithXs(splitEncoding[1], var[1])
                    else: # Compare the first and second halves, the first matching exactly, the second not matching exactly
                        splitPoint = len(splitEncoding[0])
                        firstHalf = var[1][:splitPoint]
                        secondHalf = var[1][splitPoint:]
                        # Return the first half of the encoding (before the !=) with the equivalent first half of the variable value, logically ANDed with the inverse of the second etc.
                        return (compareWithXs(splitEncoding[0],firstHalf)) and (not (compareWithXs(splitEncoding[1], secondHalf)))
                else:
                    # Compare the two strings
                    return compareWithXs(tup[1], var[1])
        return False



