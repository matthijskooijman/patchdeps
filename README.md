`patchdeps`
===========
Tool for analyzing textual dependencies within a patch series.

Given a pile of patches, `patchdeps` can find out which patch modifies
which files and lines within those files. From there, it can detect that
a specific patch modifies a line introduced by an earlier patch, and
mark these patches as dependent.

This tool is intended to sort out a pile of patches, so you can
determine which patches should be applied together as a group and which
can be freely reordered without problems.

Features
--------
 - Automatically detect coarse-grained dependencies between patches
   based on the files they modify.
 - Automatically detect fine-grained dependencies between patches
   based on the lines they modify.
 - Show which patch modifies which files.
 - Show which patch last modified which line (*i.e.* blame).
 - Output depedencies as:
    * A list
    * A textual matrix
    * A dot format graph (optionally running xdot to display it)
 - Read patches from:
    * A series of git commits (uses the git command)
    * A series of patch (diff) files

Limitations
-----------
Note that this tool can only detect textual dependencies, when two
patches touch the same line or lines close together. For logical
dependencies, where a patches applies just fine without another patch,
but does need the other to actually work, you'll still have to think
yourself :-)

Furthermore, this tool needs a proper series of patches that are known
to apply cleanly. Since it works without the original files and even
without doing the magic patch uses (offset and fuzziness) to find out
how to apply a patch, it must assume that the line numbers in the patch
are correct and works solely from that. `patchdeps` does do some
verification of the line contents it does have (mostly from context
lines) and will yell at you if it detects a problem, but it might not
catch these problems always...

Running patchdeps
-----------------
Patchdeps supports a number of commandline parameters, which are
explained when running `patchdeps --help`.

Examples
--------
This shows running patchdeps on a set of kernel cleanup patches, which
modify a single driver containing a few files. It shows both the list
output and the matrix output. In the matrix output, an `X` means the
later patch changed a line introduced by the earlier patch, while a `*`
means the later patch changes a line within two lines of a line changed
by the earlier patch (number of lines can be configured with the
`--proximity` flag).

    $ patchdeps --git 025a9230c8373..91121c103ae93 --list --matrix
    d6ec53e04bf79 (staging: dwc2: simplify register shift expressions) depends on:
      f923463335385 (staging: dwc2: unshift non-bool register value constants) (proximity)
    08b9f9db707ba (staging: dwc2: remove redundant register reads) depends on:
      c35205aa05124 (staging: dwc2: re-use hptxfsiz variable) (hard)
    a1fc524393583 (staging: dwc2: properly mask the GRXFSIZ register) depends on:
      4ab799df6d716 (staging: dwc2: remove specific fifo size constants) (proximity)
      c35205aa05124 (staging: dwc2: re-use hptxfsiz variable) (proximity)
      08b9f9db707ba (staging: dwc2: remove redundant register reads) (hard)
    9badec2f9fa92 (staging: dwc2: interpret all hwcfg and related register at init time) depends on:
      3b9edf88472e9 (staging: dwc2: fix off-by-one in check for max_packet_count parameter) (hard)
      f923463335385 (staging: dwc2: unshift non-bool register value constants) (hard)
      1c58ce133971e (staging: dwc2: only read the snpsid register once) (hard)
      a1fc524393583 (staging: dwc2: properly mask the GRXFSIZ register) (hard)
    de4a193193989 (staging: dwc2: validate the value for phy_utmi_width) depends on:
      9badec2f9fa92 (staging: dwc2: interpret all hwcfg and related register at init time) (proximity)
    4ab799df6d716 (staging: dwc2: remove specific fifo size constants)  ····················*    
    3b9edf88472e9 (staging: dwc2: fix off-by-one in check for max_packet_count param  ······│·X  
    f923463335385 (staging: dwc2: unshift non-bool register value constants)  ··········*···│·X  
    1c58ce133971e (staging: dwc2: only read the snpsid register once)  ·················│···│·X  
    d6ec53e04bf79 (staging: dwc2: simplify register shift expressions)  ────────────────┘   │ │  
    acdb9046b61a6 (staging: dwc2: add missing shift)                                        │ │  
    57bb8aeda06af (staging: dwc2: simplify debug output in dwc_hc_init)                     │ │  
    c35205aa05124 (staging: dwc2: re-use hptxfsiz variable)  ·····························X·* │  
    08b9f9db707ba (staging: dwc2: remove redundant register reads)  ──────────────────────┘·X │  
    a1fc524393583 (staging: dwc2: properly mask the GRXFSIZ register)  ─────────────────────┘·X  
    9badec2f9fa92 (staging: dwc2: interpret all hwcfg and related register at init t  ────────┘·*
    de4a193193989 (staging: dwc2: validate the value for phy_utmi_width)  ──────────────────────┘
    91121c103ae93 (staging: dwc2: make dwc2_core_params documentation more complete)             

The above uses by-line analysis. A per-file analysis is also available,
but as you can see below, it is not so useful for this example (since
there are only a handful of files in the driver, pretty much every patch
depends on every other patch:

    $ patchdeps --git 025a9230c8373..91121c103ae93 --matrix --by-file
    4ab799df6d716 (staging: dwc2: remove specific fifo size constants)  ················X·············X···X  
    3b9edf88472e9 (staging: dwc2: fix off-by-one in check for max_packet_count param  ··X···X···X·X·X·X·X·X  
    f923463335385 (staging: dwc2: unshift non-bool register value constants)  ──────────┘·X·X·X·X·X·X·X·X·X  
    1c58ce133971e (staging: dwc2: only read the snpsid register once)  ───────────────────┘·X·X·│·│·│·│·X │  
    d6ec53e04bf79 (staging: dwc2: simplify register shift expressions)  ────────────────────┘·X·X·X·X·X·X·X  
    acdb9046b61a6 (staging: dwc2: add missing shift)  ────────────────────────────────────────┘·│·│·│·│·X │  
    57bb8aeda06af (staging: dwc2: simplify debug output in dwc_hc_init)  ───────────────────────┘·X·X·X·X·X  
    c35205aa05124 (staging: dwc2: re-use hptxfsiz variable)  ─────────────────────────────────────┘·X·X·X·X  
    08b9f9db707ba (staging: dwc2: remove redundant register reads)  ────────────────────────────────┘·X·X·X  
    a1fc524393583 (staging: dwc2: properly mask the GRXFSIZ register)  ───────────────────────────────┘·X·X  
    9badec2f9fa92 (staging: dwc2: interpret all hwcfg and related register at init t  ──────────────────┘·X·X
    de4a193193989 (staging: dwc2: validate the value for phy_utmi_width)  ────────────────────────────────┘·X
    91121c103ae93 (staging: dwc2: make dwc2_core_params documentation more complete)  ──────────────────────┘

Copyright & License
-------------------
© 2013 Matthijs Kooijman <<matthijs@stdin.nl>>

Patchdeps is made available under the MIT license

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
